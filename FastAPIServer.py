import os
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional, Dict, Any
from datetime import date, timedelta
import json
import re
import time
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

# Cargar variables de entorno desde .env
load_dotenv()

# --- Configuración global del modelo Gemini ---
try:
    gemini_api_key = os.environ["GOOGLE_API_KEY"]
except KeyError:
    raise ValueError("La variable de entorno 'GOOGLE_API_KEY' no está configurada. Asegúrate de tener un archivo .env con tu clave.")

genai.configure(api_key=gemini_api_key)

generation_config_global = genai.types.GenerationConfig(
    temperature=0.7,
    top_p=0.95,
    top_k=64,
    max_output_tokens=2048,
)

safety_settings_global = [
    {"category": HarmCategory.HARM_CATEGORY_HARASSMENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
    {"category": HarmCategory.HARM_CATEGORY_HATE_SPEECH, "threshold": HarmBlockThreshold.BLOCK_NONE},
    {"category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, "threshold": HarmBlockThreshold.BLOCK_NONE},
    {"category": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
]

# --- Modelos Pydantic ---
class Devocional(BaseModel):
    id: str
    date: date
    language: str
    version: str
    versiculo: str
    reflexion: str
    para_meditar: List[str]
    oracion: str
    tags: List[str]

class ApiResponseData(BaseModel):
    es: Dict[str, List[Devocional]] = Field(default_factory=dict)
    en: Dict[str, List[Devocional]] = Field(default_factory=dict)

class ApiResponse(BaseModel):
    status: str
    message: str
    data: ApiResponseData = Field(default_factory=ApiResponseData)

class GenerateRequest(BaseModel):
    start_date: date
    end_date: date
    master_lang: str = "es"
    master_version: str = "RVR1960"
    other_versions: Dict[str, List[str]] = Field(default_factory=dict)
    topic: Optional[str] = None
    main_verse_hint: Optional[str] = None

# --- Custom Exception para manejo de reintentos ---
class GeminiRetryError(Exception):
    """Excepción personalizada para errores que deberían activar un reintento."""
    pass

# --- Funciones de Utilidad ---
def parse_gemini_response(response_text: str, excluded_verses: set) -> Dict[str, Any]:
    """
    Parsea la respuesta JSON de Gemini para extraer el devocional.
    Verifica si el versículo principal está en excluded_verses y lanza GeminiRetryError si está repetido.
    Corrige la posición de la referencia del versículo y el ID basado en el versículo principal.
    """
    print(f"DEBUG: Iniciando parseo de respuesta. Excluded verses: {excluded_verses}")
    json_string = response_text
    match = re.search(r"```json\s*(\{.*\})\s*```", response_text, re.DOTALL)
    if match:
        json_string = match.group(1)
    
    json_string = json_string.strip()
    if not json_string.startswith("{") or not json_string.endswith("}"):
        first_brace = json_string.find("{")
        last_brace = json_string.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            json_string = json_string[first_brace:last_brace + 1]
            print(f"INFO: JSON ajustado a partir de corchetes: {json_string[:100]}...")

    try:
        data = json.loads(json_string)
        
        expected_keys = ["id", "date", "language", "version", "versiculo", "reflexion", "para_meditar", "oracion", "tags"]
        if not all(k in data for k in expected_keys):
            missing_keys = [k for k in expected_keys if k not in data]
            print(f"ERROR: Claves esperadas faltantes en el JSON de Gemini: {missing_keys}")
            raise ValueError(f"La estructura del devocional JSON no contiene todas las claves esperadas. Faltan: {', '.join(missing_keys)}")
        
        # --- Validación de tipos de datos ---
        if not isinstance(data["versiculo"], str):
            print(f"ERROR: El campo 'versiculo' no es una cadena: {type(data['versiculo'])}")
            raise ValueError("El campo 'versiculo' debe ser una cadena.")
        if not isinstance(data["reflexion"], str):
            print(f"ERROR: El campo 'reflexion' no es una cadena: {type(data['reflexion'])}")
            raise ValueError("El campo 'reflexion' debe ser una cadena.")
        if not isinstance(data["para_meditar"], list):
            print(f"ERROR: El campo 'para_meditar' no es una lista: {type(data['para_meditar'])}")
            raise ValueError("El campo 'para_meditar' debe ser una lista.")
        if not isinstance(data["oracion"], str):
            print(f"ERROR: El campo 'oracion' no es una cadena: {type(data['oracion'])}")
            raise ValueError("El campo 'oracion' debe ser una cadena.")
        if not isinstance(data["tags"], list):
            print(f"ERROR: El campo 'tags' no es una lista: {type(data['tags'])}")
            raise ValueError("El campo 'tags' debe ser una lista.")

        # --- Lógica para asegurar que la referencia del versículo principal esté al inicio ---
        versiculo_text = data.get("versiculo", "")
        verse_ref_pattern = r"([123]?[A-Za-záéíóúÁÉÍÓÚñÑ]+\s+\d+:\d+(?:-\d+)?)"
        all_refs = re.findall(verse_ref_pattern, versiculo_text, re.IGNORECASE)
        
        main_reference = None
        if all_refs:
            main_reference = all_refs[0].strip()
            print(f"DEBUG: Versículo principal detectado: {main_reference}")
            if main_reference in excluded_verses:
                print(f"ERROR: Versículo repetido encontrado: {main_reference}")
                raise GeminiRetryError(f"Versículo {main_reference} ya usado. Necesita regenerarse.")
            cleaned_versiculo = re.sub(r"\s*\(?" + re.escape(main_reference) + r"\)?\.?\s*", "", versiculo_text, 1, re.IGNORECASE).strip()
            if not cleaned_versiculo:
                print(f"ERROR: Texto del versículo vacío después de limpiar la referencia: {versiculo_text}")
                raise GeminiRetryError("El versículo principal no contiene texto válido. Necesita regenerarse.")
            if cleaned_versiculo.startswith(':'):
                cleaned_versiculo = cleaned_versiculo[1:].strip()
            if cleaned_versiculo.startswith('.'):
                cleaned_versiculo = cleaned_versiculo[1:].strip()
            data["versiculo"] = f"{main_reference}: {cleaned_versiculo} ({data['version']})"
            print(f"INFO: Versículo principal corregido/reformulado a: {data['versiculo']}")
            
            # Corregir el ID basado en el versículo principal
            book = main_reference.split()[0][:5].lower()
            chapter_verse = ''.join(main_reference.split()[1:]).replace(':', '')
            data["id"] = f"{book}{chapter_verse}{data['version']}"
            print(f"INFO: ID corregido a: {data['id']}")
        else:
            print(f"ADVERTENCIA: No se encontró referencia bíblica en el versículo: '{versiculo_text}'")
            raise GeminiRetryError("No se encontró referencia bíblica válida en el versículo principal. Necesita regenerarse.")

        # --- Lógica para corregir la posición de la referencia en 'para_meditar' ---
        para_meditar_list = data.get("para_meditar", [])
        corrected_para_meditar = []
        for item in para_meditar_list:
            if isinstance(item, str):
                all_refs_meditar = re.findall(verse_ref_pattern, item, re.IGNORECASE)
                if all_refs_meditar:
                    meditar_reference = all_refs_meditar[0].strip()
                    cleaned_item = re.sub(r"\s*\(?" + re.escape(meditar_reference) + r"\)?\.?\s*", "", item, 1, re.IGNORECASE).strip()
                    if cleaned_item.startswith(':'):
                        cleaned_item = cleaned_item[1:].strip()
                    if cleaned_item.startswith('.'):
                        cleaned_item = cleaned_item[1:].strip()
                    corrected_para_meditar.append(f"{meditar_reference}: {cleaned_item}")
                    print(f"INFO: Versículo 'para_meditar' corregido/reformulado: {meditar_reference}: {cleaned_item}")
                else:
                    corrected_para_meditar.append(item)
                    print(f"ADVERTENCIA: No se encontró referencia bíblica en 'para_meditar' item: '{item}'")
            else:
                corrected_para_meditar.append(str(item))
                print(f"ADVERTENCIA: Elemento de 'para_meditar' no es una cadena: {item}")
        data["para_meditar"] = corrected_para_meditar
        
        print(f"DEBUG: Datos parseados: {json.dumps(data, indent=2, ensure_ascii=False)[:500]}...")
        return data
        
    except json.JSONDecodeError as e:
        print(f"ERROR: La respuesta de la API no es un JSON válido después de la extracción: {e}. Texto original: {response_text[:500]}... Texto JSON intentado: {json_string[:500]}...")
        raise GeminiRetryError(f"JSON inválido: {str(e)}")
    except ValueError as e:
        print(f"ERROR: Falló la validación de la estructura del devocional: {e}. Texto JSON: {json_string[:500]}...")
        raise GeminiRetryError(f"Estructura inválida: {str(e)}")
    except GeminiRetryError:
        raise
    except Exception as e:
        print(f"ERROR inesperado al parsear la respuesta de Gemini: {e}. Texto original: {response_text[:500]}... Texto JSON intentado: {json_string[:500]}...")
        raise GeminiRetryError(f"Error inesperado en parseo: {str(e)}")

def create_devocional_from_api_response(devocional_data: Dict[str, Any]) -> Devocional:
    """Crea un objeto Devocional a partir de un diccionario de respuesta de la API."""
    print(f"DEBUG: Iniciando creación de Devocional con datos: {json.dumps(devocional_data, indent=2, ensure_ascii=False)[:500]}...")
    try:
        # Convertir la fecha si es necesario
        if isinstance(devocional_data.get("date"), str):
            try:
                devocional_data["date"] = date.fromisoformat(devocional_data["date"])
            except ValueError as e:
                print(f"ERROR: Formato de fecha inválido: {devocional_data['date']}. Error: {e}")
                raise ValueError(f"Formato de fecha inválido: {e}")
        
        # Asegurar que para_meditar y tags sean listas
        if not isinstance(devocional_data.get("para_meditar"), list):
            print(f"WARNING: 'para_meditar' no es una lista, convirtiendo: {devocional_data['para_meditar']}")
            devocional_data["para_meditar"] = []
        if not isinstance(devocional_data.get("tags"), list):
            print(f"WARNING: 'tags' no es una lista, convirtiendo: {devocional_data['tags']}")
            devocional_data["tags"] = []

        # Validar tipos de datos
        if not isinstance(devocional_data.get("id"), str):
            print(f"ERROR: El campo 'id' no es una cadena: {type(devocional_data['id'])}")
            raise ValueError("El campo 'id' debe ser una cadena.")
        if not isinstance(devocional_data.get("language"), str):
            print(f"ERROR: El campo 'language' no es una cadena: {type(devocional_data['language'])}")
            raise ValueError("El campo 'language' debe ser una cadena.")
        if not isinstance(devocional_data.get("version"), str):
            print(f"ERROR: El campo 'version' no es una cadena: {type(devocional_data['version'])}")
            raise ValueError("El campo 'version' debe ser una cadena.")
        if not isinstance(devocional_data.get("versiculo"), str):
            print(f"ERROR: El campo 'versiculo' no es una cadena: {type(devocional_data['versiculo'])}")
            raise ValueError("El campo 'versiculo' debe ser una cadena.")
        if not isinstance(devocional_data.get("reflexion"), str):
            print(f"ERROR: El campo 'reflexion' no es una cadena: {type(devocional_data['reflexion'])}")
            raise ValueError("El campo 'reflexion' debe ser una cadena.")
        if not isinstance(devocional_data.get("oracion"), str):
            print(f"ERROR: El campo 'oracion' no es una cadena: {type(devocional_data['oracion'])}")
            raise ValueError("El campo 'oracion' debe ser una cadena.")

        # Crear el objeto Devocional
        devotional = Devocional(**devocional_data)
        print(f"DEBUG: Devocional creado exitosamente: {devotional.id}")
        return devotional
    except ValidationError as e:
        print(f"ERROR: Validación de Pydantic fallida: {e}. Datos: {json.dumps(devocional_data, indent=2, ensure_ascii=False)[:500]}...")
        raise GeminiRetryError(f"Error de validación de Pydantic: {str(e)}")
    except Exception as e:
        print(f"ERROR: Error al crear objeto Devocional: {e}. Datos: {json.dumps(devocional_data, indent=2, ensure_ascii=False)[:500]}...")
        raise GeminiRetryError(f"Error al crear Devocional: {str(e)}")

def create_error_devocional(
    target_date: date,
    lang: str,
    version: str,
    error_message: str,
    id_suffix: str = "error"
) -> Devocional:
    """Crea un objeto Devocional con información de error."""
    print(f"DEBUG: Creando devocional de error para {target_date}: {error_message}")
    return Devocional(
        id=f"{target_date.strftime('%Y%m%d')}_{lang}_{version}_{id_suffix}",
        date=target_date,
        language=lang,
        version=version,
        versiculo=f"Error al generar ({error_message})",
        reflexion=f"Hubo un problema al generar el devocional para esta fecha. Mensaje de error: {error_message}",
        para_meditar=[],
        oracion="Señor, ayúdanos a comprender Tu palabra incluso cuando no podamos generarla. Amén.",
        tags=["Error", "Internal"]
    )

EXCLUDED_VERSES_FILE = "excluded_verses.json"
excluded_verses = set()

def load_excluded_verses():
    global excluded_verses
    if os.path.exists(EXCLUDED_VERSES_FILE):
        try:
            with open(EXCLUDED_VERSES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict) and "versiculos_excluidos" in data:
                    excluded_verses = set(data.get("versiculos_excluidos", []))
                elif isinstance(data, list):
                    print(f"INFO: Formato antiguo detectado en excluded_verses.json. Convirtiendo lista a conjunto.")
                    excluded_verses = set(data)
                else:
                    print(f"ERROR: Formato inválido en excluded_verses.json. Inicializando como vacío.")
                    excluded_verses = set()
            print(f"INFO: Versículos excluidos cargados: {len(excluded_verses)} - {excluded_verses}")
        except Exception as e:
            print(f"ERROR: No se pudo cargar excluded_verses.json: {e}. Inicializando como vacío.")
            excluded_verses = set()
    else:
        print(f"INFO: No se encontró excluded_verses.json, inicializando como vacío")
        excluded_verses = set()

def save_excluded_verses():
    try:
        print(f"DEBUG: Intentando guardar excluded_verses: {excluded_verses}")
        with open(EXCLUDED_VERSES_FILE, 'w', encoding='utf-8') as f:
            json.dump({"versiculos_excluidos": list(excluded_verses)}, f, indent=4, ensure_ascii=False)
        print(f"INFO: Versículos excluidos guardados: {len(excluded_verses)} - {excluded_verses}")
    except Exception as e:
        print(f"ERROR: No se pudo guardar excluded_verses.json: {e}")

# --- Clase PromptBuilder ---
class PromptBuilder:
    def __init__(self, lang: str, version: str, target_date: date, topic: Optional[str], main_verse_hint: Optional[str], excluded_verses: set = None):
        self.lang = lang
        self.version = version
        self.target_date = target_date
        self.topic = topic
        self.main_verse_hint = main_verse_hint
        self.excluded_verses = excluded_verses or set()

    def build_prompt(self) -> str:
        prompt_content = f"""El formato JSON exacto que debes devolver para UN ÚNICO devocional es:
```json
{{
  "id": "[BOOK][CHAPTER][VERSE]{self.version}",
  "date": "{self.target_date.strftime('%Y-%m-%d')}",
  "language": "{self.lang}",
  "version": "{self.version}",
  "versiculo": "[BOOK CHAPTER:VERSE]: [TEXT] ({self.version})",
  "reflexion": "[REFLEXION]",
  "para_meditar": ["[BOOK CHAPTER:VERSE]: [TEXT]", "[BOOK CHAPTER:VERSE]: [TEXT]"],
  "oracion": "[ORACION]",
  "tags": ["[TAG_1]", "[TAG_2]"]
}}
```

Quiero un devocional cristiano diario con la siguiente estructura JSON, para la fecha {self.target_date.strftime('%Y-%m-%d')}.
El devocional debe estar en {self.lang} y usar la versión bíblica {self.version}.

Consideraciones importantes:

- Versículo Bíblico Único: Iniciar con una cita bíblica válida del Nuevo Testamento (ej. Juan 3:16: [texto]). La referencia debe ser precisa y existente (verifica que el capítulo y versículo existan en el libro especificado). NO uses los siguientes versículos: {', '.join(self.excluded_verses) if self.excluded_verses else 'Ninguno'}.
- Reflexión Profunda: Una reflexión que explore el significado del versículo y su aplicación práctica a la vida diaria, con un enfoque cristocéntrico. Debe ser concisa, no más de 300 palabras, y tener un tono de estudio bíblico y devocional.
- Para meditar: Al menos dos versículos relacionados pero diferentes al versículo principal, misma versión de la Biblia, con formato [BOOK CHAPTER:VERSE]: [TEXT]. Asegúrate de que las referencias sean válidas.
- Oración: Una oración final relacionada con el tema del devocional de aproximadamente 200 palabras, terminando siempre con "En el nombre de Jesús, amén".
- Tags Relevantes: 2 tags relevantes para el devocional, cada uno de una sola palabra.
- ID Formato: El id debe ser único, usando los primeros 5 caracteres del nombre del libro (en minúsculas) + capítulo + versículo + siglas de traducción sin dos puntos (ej. Juan 3:16 es juan316RVR1960).
"""
        if self.topic:
            prompt_content += f"\nEl devocional debe enfocarse en el tema: {self.topic}."
        if self.main_verse_hint:
            prompt_content += f"\nConsidera usar o hacer referencia al versículo: {self.main_verse_hint}."
        
        return prompt_content.strip()

# --- Instancia de FastAPI ---
app = FastAPI()

# --- Endpoint para generar devocionales ---
@app.post("/generate_devotionals", response_model=ApiResponse)
@retry(
    retry=retry_if_exception_type(GeminiRetryError),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    stop=stop_after_attempt(3)
)
async def generate_devotionals(request: GenerateRequest):
    try:
        load_excluded_verses()
        print(f"DEBUG: Estado inicial de excluded_verses: {excluded_verses}")
        response_data = ApiResponseData()
        current_date = request.start_date
        delta = timedelta(days=1)

        while current_date <= request.end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            response_data.es[date_str] = []
            
            try:
                prompt_builder = PromptBuilder(
                    lang=request.master_lang,
                    version=request.master_version,
                    target_date=current_date,
                    topic=request.topic,
                    main_verse_hint=request.main_verse_hint,
                    excluded_verses=excluded_verses
                )
                prompt = prompt_builder.build_prompt()
                print(f"DEBUG: Prompt generado para {date_str}: {prompt[:200]}...")
                
                model = genai.GenerativeModel('gemini-2.0-flash-lite')
                response = model.generate_content(
                    prompt,
                    generation_config=generation_config_global,
                    safety_settings=safety_settings_global
                )
                
                print(f"DEBUG: Respuesta de Gemini recibida: {response.text[:200]}...")
                devocional_data = parse_gemini_response(response.text, excluded_verses)
                devotional = create_devocional_from_api_response(devocional_data)
                
                verse_ref_pattern = r"([123]?[A-Za-záéíóúÁÉÍÓÚñÑ]+\s+\d+:\d+(?:-\d+)?)"
                main_reference = re.findall(verse_ref_pattern, devotional.versiculo, re.IGNORECASE)
                if main_reference:
                    print(f"DEBUG: Añadiendo versículo {main_reference[0]} a excluded_verses")
                    excluded_verses.add(main_reference[0].strip())
                    save_excluded_verses()
                
                response_data.es[date_str].append(devotional)
            
            except GeminiRetryError as gre:
                print(f"WARNING: Intento fallido para {date_str}: {gre}. Generando devocional de error.")
                error_devotional = create_error_devocional(
                    target_date=current_date,
                    lang=request.master_lang,
                    version=request.master_version,
                    error_message=str(gre)
                )
                response_data.es[date_str].append(error_devotional)
            
            except Exception as e:
                print(f"WARNING: Error al generar devocional para {date_str}: {e}. Generando devocional de error.")
                error_devotional = create_error_devocional(
                    target_date=current_date,
                    lang=request.master_lang,
                    version=request.master_version,
                    error_message=str(e)
                )
                response_data.es[date_str].append(error_devotional)
            
            current_date += delta
        
        print(f"DEBUG: Estado final de excluded_verses: {excluded_verses}")
        return ApiResponse(
            status="success",
            message="Devocionales generados correctamente",
            data=response_data
        )

    except Exception as e:
        print(f"ERROR: Error inesperado en generate_devotionals: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al generar devocionales: {str(e)}"
        )
    finally:
        print(f"DEBUG: Guardando excluded_verses en finally: {excluded_verses}")
        save_excluded_verses()
