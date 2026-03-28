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
import random

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

# --- Instancia de FastAPI ---
app = FastAPI(
    title="Generador de Devocionales API",
    description="API para generar devocionales bíblicos usando Google Gemini.",
    version="1.0.0",
)

# --- Variables globales y de estado ---
# Ruta al archivo de versículos excluidos
EXCLUDED_VERSES_FILE = "excluded_verses.json"
excluded_verses: set[str] = set()

# Mapeo de nombres completos de libros del Nuevo Testamento a acrónimos
BOOK_ABBREVIATIONS = {
    "Mateo": "Mt", "Marcos": "Mc", "Lucas": "Lc", "Juan": "Jn", "Hechos": "Hch",
    "Romanos": "Ro", "1 Corintios": "1 Co", "2 Corintios": "2 Co", "Gálatas": "Ga",
    "Efesios": "Ef", "Filipenses": "Flp", "Colosenses": "Col",
    "1 Tesalonicenses": "1 Ts", "2 Tesalonicenses": "2 Ts", "1 Timoteo": "1 Ti",
    "2 Timoteo": "2 Ti", "Tito": "Tit", "Filemón": "Flm", "Hebreos": "He",
    "Santiago": "Stg", "1 Pedro": "1 P", "2 Pedro": "2 P", "1 Juan": "1 Jn",
    "2 Juan": "2 Jn", "3 Juan": "3 Jn", "Judas": "Jds", "Apocalipsis": "Ap"
}

# Carga inicial de versículos excluidos
def load_excluded_verses():
    """Carga la lista de versículos excluidos desde un archivo JSON."""
    global excluded_verses
    if os.path.exists(EXCLUDED_VERSES_FILE):
        with open(EXCLUDED_VERSES_FILE, 'r', encoding='utf-8') as f:
            try:
                loaded_verses = json.load(f)
                if isinstance(loaded_verses, list):
                    excluded_verses = set(loaded_verses)
                    print(f"INFO: Versículos excluidos cargados: {len(excluded_verses)} - {excluded_verses}")
                else:
                    print(f"ADVERTENCIA: El archivo '{EXCLUDED_VERSES_FILE}' no contiene una lista. Reiniciando lista de excluidos.")
                    excluded_verses = set()
            except json.JSONDecodeError:
                print(f"ERROR: Fallo al decodificar JSON de '{EXCLUDED_VERSES_FILE}'. El archivo puede estar corrupto. Reiniciando lista de excluidos.")
                excluded_verses = set()
    else:
        print("INFO: No se encontró el archivo de versículos excluidos. Iniciando con una lista vacía.")
        excluded_verses = set()

def save_excluded_verses():
    """Guarda la lista de versículos excluidos en un archivo JSON."""
    with open(EXCLUDED_VERSES_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(excluded_verses), f, ensure_ascii=False, indent=4)
    print(f"DEBUG: Versículos excluidos guardados: {len(excluded_verses)} - {excluded_verses}")

# Cargar versículos excluidos al iniciar la aplicación
load_excluded_verses()

# --- Modelos Pydantic para la API ---

# NUEVA CLASE: Modelo para cada ítem de "para_meditar"
class ParaMeditarItem(BaseModel):
    cita: str
    texto: str

class DevotionalContent(BaseModel):
    id: str
    date: str
    language: str
    version: str
    versiculo: str
    reflexion: str
    para_meditar: List[ParaMeditarItem] # <-- CAMBIO AQUÍ: Ahora es una lista de ParaMeditarItem
    oracion: str
    tags: List[str]

class GenerateRequest(BaseModel):
    start_date: date
    end_date: date
    master_lang: str
    master_version: str
    topic: Optional[str] = None
    main_verse_hint: Optional[str] = None # Pista para el versículo principal
    other_versions: Dict[str, List[str]] = Field(default_factory=dict)

class LanguageData(BaseModel):
    es: Dict[str, List[DevotionalContent]] = Field(default_factory=dict)
    en: Dict[str, List[DevotionalContent]] = Field(default_factory=dict)

class ApiResponse(BaseModel):
    status: str
    message: str
    data: LanguageData = Field(default_factory=LanguageData)

# --- Funciones de Utilidad ---

def create_error_devocional(date_obj: date, lang: str, version: str, error_msg: str) -> DevotionalContent:
    """Crea un objeto DevotionalContent para errores."""
    return DevotionalContent(
        id=f"error_{date_obj.strftime('%Y%m%d')}_{lang}_{version}",
        date=date_obj.strftime('%Y-%m-%d'),
        language=lang,
        version=version,
        versiculo="ERROR EN LA GENERACIÓN",
        reflexion=f"No se pudo generar el devocional para esta fecha/versión. Causa: {error_msg}.",
        para_meditar=[], # Sigue siendo una lista, ahora de ParaMeditarItem vacía
        oracion="Señor, pedimos tu guía para solucionar este problema técnico. Amén.",
        tags=["Error"]
    )

def obtener_todos_los_versiculos_posibles() -> set[str]:
    """
    Retorna un conjunto de todos los versículos posibles del Nuevo Testamento
    que tu sistema considera válidos para la generación.
    ¡IMPORTANTE: ADAPTA ESTA FUNCIÓN PARA QUE CARGUE TUS VERSÍCULOS REALES!
    Esto podría ser desde un archivo de texto, una base de datos, etc.
    """
    # Versículos del Nuevo Testamento para tu lista de posibles
    return {"Mateo 4:19","Mateo 5:13","Mateo 6:6","Mateo 7:1","Mateo 9:12","Mateo 10:32","Mateo 11:29","Mateo 13:23","Mateo 14:14","Mateo 17:20",
"Mateo 19:26","Mateo 21:22","Mateo 24:13","Mateo 25:21","Marcos 1:17","Marcos 2:17","Marcos 4:20","Marcos 5:36","Marcos 6:34","Marcos 9:23",
"Marcos 11:24","Marcos 12:30-31","Marcos 14:38","Marcos 15:39","Lucas 1:37","Lucas 4:18-19","Lucas 5:32","Lucas 7:50","Lucas 8:21","Lucas 11:9-10",
"Lucas 13:24","Lucas 14:11","Lucas 16:10","Lucas 18:27","Lucas 19:10","Lucas 21:36","Lucas 22:19-20","Lucas 23:34","Lucas 24:49","Juan 1:14",
"Juan 2:5","Juan 3:3","Juan 4:14","Juan 5:24","Juan 6:27","Juan 8:12","Juan 9:4","Juan 10:27-28","Juan 11:25-26","Juan 12:24",
"Juan 14:15","Juan 16:7-8","Juan 17:3","Juan 19:30","Hechos 3:19","Hechos 7:55-56","Hechos 9:6","Hechos 10:34-35","Hechos 16:31","Hechos 26:18",
"Romanos 1:17","Romanos 3:28","Romanos 4:3","Romanos 5:12","Romanos 6:4","Romanos 7:14","Romanos 9:15-16","Romanos 11:29","Romanos 12:15","Romanos 13:1",
"Romanos 14:10","Romanos 15:4","1 Corintios 1:2","1 Corintios 2:4","1 Corintios 3:11","1 Corintios 4:20","1 Corintios 5:7","1 Corintios 6:12","1 Corintios 7:17","1 Corintios 8:6",
"1 Corintios 9:22","1 Corintios 11:26","1 Corintios 12:1","1 Corintios 13:1","1 Corintios 14:1","1 Corintios 15:1","2 Corintios 1:20","2 Corintios 3:18","2 Corintios 4:16","2 Corintios 5:1",
"2 Corintios 6:14","2 Corintios 8:9","2 Corintios 9:6","2 Corintios 10:4-5","2 Corintios 12:9","Gálatas 1:4","Gálatas 2:16","Gálatas 3:26","Gálatas 4:7","Gálatas 5:16",
"Gálatas 6:8","Efesios 1:7","Efesios 2:19-20","Efesios 3:20","Efesios 4:1","Efesios 5:18","Efesios 6:10","Filipenses 1:21","Filipenses 2:14","Filipenses 3:20",
"Filipenses 4:6","Colosenses 1:18","Colosenses 2:10","Colosenses 3:17","1 Tesalonicenses 1:9-10","1 Tesalonicenses 2:4","1 Tesalonicenses 3:12","1 Tesalonicenses 4:3","1 Tesalonicenses 5:11","2 Tesalonicenses 1:3",
"2 Tesalonicenses 3:16","1 Timoteo 1:5","1 Timoteo 3:16","1 Timoteo 4:12","1 Timoteo 5:8","1 Timoteo 6:11","2 Timoteo 1:9","2 Timoteo 2:3-4","2 Timoteo 3:1-5","2 Timoteo 4:7-8",
"Tito 2:11-14","Filemón 1:6","Hebreos 2:17-18","Hebreos 3:13","Hebreos 4:12","Hebreos 5:8-9","Hebreos 7:25","Hebreos 9:27-28","Hebreos 10:19-22","Hebreos 12:11",
"Hebreios 13:8","Santiago 1:2-4","Santiago 2:8","Santiago 3:17","Santiago 5:16","1 Pedro 1:8-9","1 Pedro 3:18","1 Pedro 4:8","2 Pedro 1:5-7","2 Pedro 3:9",
"1 Juan 1:7","1 Juan 3:23","1 Juan 4:16","1 Juan 5:4","2 Juan 1:6","3 Juan 1:11","Judas 1:20-21","Apocalipsis 1:8","Apocalipsis 7:9-10","Apocalipsis 22:12-13",
"Mateo 3:11","Mateo 8:17","Mateo 12:20","Mateo 15:19","Mateo 17:5","Mateo 20:28","Mateo 22:21","Mateo 26:41","Marcos 7:23","Marcos 9:35",
"Marcos 10:45","Lucas 2:10-11","Lucas 4:43","Lucas 5:24","Lucas 6:46-47","Lucas 9:24","Lucas 12:31","Lucas 17:5","Lucas 20:25","Juan 1:29",
"Juan 3:17","Juan 5:30","Juan 6:40","Juan 7:17","Juan 10:11","Juan 12:47","Juan 14:21","Juan 15:13","Juan 17:17","Hechos 2:21",
"Hechos 4:20","Hechos 10:43","Hechos 13:38-39","Hechos 17:11","Romanos 2:13","Romanos 4:5","Romanos 5:17","Romanos 6:14","Romanos 8:1","Romanos 9:28",
"Romanos 11:6","Romanos 12:10","Romanos 13:9","Romanos 14:17","Romanos 15:13","1 Corintios 1:30","1 Corintios 2:16","1 Corintios 3:13","1 Corintios 4:7","1 Corintios 6:11",
"1 Corintios 7:23","1 Corintios 8:9","1 Corintios 9:19","1 Corintios 10:23","1 Corintios 11:23-25","1 Corintios 12:4-6","1 Corintios 13:7","1 Corintios 14:26","1 Corintios 15:20","2 Corintios 1:9",
"2 Corintios 3:6","2 Corintios 4:6","2 Corintios 5:7","2 Corintios 6:16","2 Corintios 8:12","2 Corintios 9:10","2 Corintios 11:3","2 Corintios 12:10","Gálatas 1:10","Gálatas 2:20",
"Gálatas 3:11","Gálatas 4:19","Gálatas 5:25","Gálatas 6:1","Gálatas 6:10","Efesios 1:11","Efesios 2:4-5","Efesios 3:16","Efesios 4:14","Efesios 5:2",
"Efesios 6:11","Filipenses 1:6","Filipenses 2:3","Filipenses 3:10","Filipenses 4:7","Colosenses 1:27","Colosenses 2:9","Colosenses 3:10","1 Tesalonicenses 1:5","1 Tesalonicenses 2:19",
"1 Tesalonicenses 4:1","1 Tesalonicenses 5:5","1 Tesalonicenses 5:22","2 Tesalonicenses 2:1-2","2 Tesalonicenses 3:1","1 Timoteo 1:16","1 Timoteo 3:9","1 Timoteo 4:8","1 Timoteo 6:6","2 Timoteo 1:12",
"2 Timoteo 2:13","2 Timoteo 3:16","2 Timoteo 4:2","Tito 3:5","Hebreos 2:9","Hebreos 3:1","Hebreos 4:14","Hebreos 6:1","Hebreos 8:6","Hebreos 10:14",
"Hebreos 11:3","Hebreos 12:7","Hebreos 13:5","Santiago 1:6","Santiago 2:26","Santiago 4:2","1 Pedro 1:22","1 Pedro 2:2","1 Pedro 3:9","1 Pedro 4:10",
"2 Pedro 1:8","2 Pedro 3:18","1 Juan 1:8","1 Juan 3:16","1 Juan 4:18","1 Juan 5:14","Apocalipsis 1:7","Apocalipsis 7:12","Apocalipsis 20:6","Apocalipsis 22:7",
"Mateo 5:44","Mateo 7:12","Mateo 10:16","Mateo 12:31","Mateo 18:3","Mateo 21:42","Mateo 26:39","Marcos 3:35","Marcos 8:36","Marcos 10:27",
"Marcos 14:36","Lucas 1:45","Lucas 4:4","Lucas 6:27-28","Lucas 8:15","Lucas 11:28","Lucas 13:30","Lucas 16:13","Lucas 18:14","Lucas 22:42",
"Juan 1:4","Juan 3:30","Juan 5:45","Juan 6:51","Juan 8:36","Juan 11:40","Juan 14:12","Juan 15:16","Juan 17:20-21","Hechos 1:5",
"Hechos 3:26","Hechos 7:60","Hechos 10:38","Hechos 16:30","Hechos 20:24","Romanos 3:20","Romanos 4:13","Romanos 5:5","Romanos 6:17-18","Romanos 8:6",
"Romanos 9:33","Romanos 11:25-26","Romanos 12:14","Romanos 13:10","Romanos 14:23","Romanos 15:7","1 Corintios 1:25","1 Corintios 2:9","1 Corintios 3:18","1 Corintios 4:8",
"1 Corintios 6:19","1 Corintios 7:31","1 Corintios 8:1","1 Corintios 9:27","1 Corintios 10:31","1 Corintios 11:28","1 Corintios 12:27","1 Corintios 13:13","1 Corintios 14:33","1 Corintios 15:35",
"2 Corintios 1:21-22","2 Corintios 3:17","2 Corintios 4:17","2 Corintios 5:14","2 Corintios 7:1","2 Corintios 8:7","2 Corintios 9:15","2 Corintios 11:14","2 Corintios 13:4","Gálatas 2:2",
"Gálatas 3:22","Gálatas 4:5","Gálatas 5:6","Gálatas 6:1","Gálatas 6:10","Efesios 1:13-14","Efesios 2:10","Efesios 3:18-19","Efesios 4:15","Efesios 5:16",
"Efesios 6:12","Filipenses 1:27","Filipenses 2:5","Filipenses 3:14","Filipenses 4:8","Colosenses 1:27","Colosenses 2:9","Colosenses 3:10","1 Tesalonicenses 1:5","1 Tesalonicenses 2:19",
"1 Tesalonicenses 4:1","1 Tesalonicenses 5:5","1 Tesalonicenses 5:22","2 Tesalonicenses 2:1-2","2 Tesalonicenses 3:1","1 Timoteo 1:16","1 Timoteo 3:9","1 Timoteo 4:8","1 Timoteo 6:6","2 Timoteo 1:12",
"2 Timoteo 2:13","2 Timoteo 3:17","2 Timoteo 4:2","Tito 3:5","Hebreos 2:1","Hebreos 3:6","Hebreos 4:14","Hebreos 6:1","Hebreos 8:6","Hebreos 10:14",
"Hebreos 11:3","Hebreos 12:7","Hebreos 13:5","Santiago 1:12","Santiago 3:2","Santiago 4:7","1 Pedro 1:15-16","1 Pedro 2:21","1 Pedro 3:14","1 Pedro 5:8",
"2 Pedro 2:9","2 Pedro 3:13","1 Juan 2:6","1 Juan 4:1","1 Juan 4:7","1 Juan 5:18","Apocalipsis 1:7","Apocalipsis 7:12","Apocalipsis 20:6","Apocalipsis 22:7"
}

def get_abbreviated_verse_citation(full_verse_citation: str) -> str:
    """
    Convierte una cita de versículo con nombre de libro completo a su acrónimo.
    Ej: "Juan 3:16" -> "Jn 3:16"
    """
    # Maneja libros con números (ej. "1 Juan", "2 Corintios")
    # Busca el primer dígito si el primer elemento es un número, o el primer espacio
    match = re.match(r'(\d?\s*[A-ZÁÉÍÓÚÜÑa-záéíóüñ]+\s?\d*)\s*(.*)', full_verse_citation)
    if not match:
        return full_verse_citation # Retorna original si no puede parsear

    book_name_part = match.group(1).strip()
    rest_of_citation = match.group(2).strip()

    # Normaliza el nombre del libro para la búsqueda en el mapeo
    for full_name, abbrev in BOOK_ABBREVIATIONS.items():
        if full_name.lower() == book_name_part.lower(): # Coincidencia exacta (ignorando caso)
            return f"{abbrev} {rest_of_citation}"
        
        # Si el libro_name_part es como "Corintios" y full_name es "1 Corintios"
        if full_name.lower().endswith(book_name_part.lower()) and \
           full_name.lower().replace('1 ', '').replace('2 ', '').replace('3 ', '') == book_name_part.lower():
            return f"{abbrev} {rest_of_citation}"
    
    # Si no se encontró un mapeo, usa el nombre completo extraído
    return f"{book_name_part} {rest_of_citation}"


def extract_verse_from_content(content: str) -> Optional[str]:
    """
    Extrae el versículo principal del contenido generado por Gemini.
    Esta versión es más robusta para manejar la omisión del número ordinal del libro por Gemini
    y reconstruir el formato exacto esperado.
    """
    # Patrón para capturar el nombre del libro (con o sin número inicial), capítulo y versículo(s).
    # Este regex intenta ser flexible con los espacios y captura las partes clave.
    # Grupo 1: Opcional (1, 2, 3) y nombre del libro (ej. "1 Juan", "Juan", "Corintios")
    # Grupo 2: Números de capítulo y versículo(s) (ej. "3:16", "5:16-18")
    # re.IGNORECASE para hacer la búsqueda insensible a mayúsculas/minúsculas
    match = re.search(r'((?:[123]\s)?(?:[A-ZÁÉÍÓÚÜÑa-záéíóüñ]+\s?)+)\s*(\d+:\d+(?:-\d+)?)', content, re.IGNORECASE)

    if not match:
        print(f"DEBUG: No se pudo extraer el patrón de versículo del contenido: {content[:100]}...")
        return None

    book_raw = match.group(1).strip() # Ej. "1 Corintios" o "Corintios" o "Juan"
    chapter_verse_raw = match.group(2).strip() # Ej. "10:13" o "5:16-18"

    # Tu lista de nombres canónicos de libros del Nuevo Testamento (de obtener_todos_los_versiculos_posibles)
    # la usaremos para encontrar el nombre exacto.
    canonical_nt_books = {
        "Mateo", "Marcos", "Lucas", "Juan", "Hechos", "Romanos",
        "1 Corintios", "2 Corintios", "Gálatas", "Efesios", "Filipenses", "Colosenses",
        "1 Tesalonicenses", "2 Tesalonicenses", "1 Timoteo", "2 Timoteo", "Tito", "Filemón",
        "Hebreos", "Santiago", "1 Pedro", "2 Pedro", "1 Juan", "2 Juan", "3 Juan", "Judas",
        "Apocalipsis"
    }

    normalized_book_name = None

    # Iterar sobre los nombres canónicos para encontrar la mejor coincidencia
    for canonical_name in canonical_nt_books:
        # 1. Coincidencia exacta (ignorando caso)
        if book_raw.lower() == canonical_name.lower():
            normalized_book_name = canonical_name
            break
        
        # 2. Coincidencia donde el libro extraído es la parte sin número del canónico
        # Ej: book_raw="Corintios", canonical_name="1 Corintios"
        canonical_name_without_num = canonical_name.replace('1 ', '').replace('2 ', '').replace('3 ', '')
        if book_raw.lower() == canonical_name_without_num.lower():
            # Si hay múltiples opciones (ej. "Corintios" podría ser 1 o 2),
            # preferimos el que tenga un número si el original no lo tuvo
            # o podemos añadir lógica para elegir el "1" por defecto si es ambiguo.
            # Por ahora, simplemente tomamos la primera coincidencia.
            normalized_book_name = canonical_name
            break

    if not normalized_book_name:
        # Último recurso: si no se normalizó a un nombre canónico, usamos lo que se extrajo directamente
        # Esto cubrirá casos donde Gemini inventa un libro o no lo normalizamos
        normalized_book_name = book_raw
        print(f"ADVERTENCIA: No se pudo normalizar el nombre del libro '{book_raw}'. Usando tal cual.")


    # Reconstruir el versículo completo en el formato esperado
    full_verse = f"{normalized_book_name} {chapter_verse_raw}"
    
    # Algunas limpiezas finales si hay espacios extra (ej. "1 Corintios 10 :13")
    full_verse = re.sub(r'\s*:\s*', ':', full_verse) # Quita espacios alrededor de los dos puntos
    full_verse = re.sub(r'\s+', ' ', full_verse).strip() # Normaliza múltiples espacios a uno solo

    print(f"DEBUG: Versículo extraído y normalizado: '{full_verse}'")
    return full_verse


def seleccionar_versiculo_para_generacion(excluded_verses_set: set[str], main_verse_hint: Optional[str] = None) -> str:
    """
    Selecciona un versículo que no esté en la lista de excluidos.
    Prioriza el 'main_verse_hint' si es válido y no está excluido.
    """
    all_possible_verses = obtener_todos_los_versiculos_posibles()
    
    # Excluir de la lista de posibles los que ya están en excluded_verses_set
    available_verses = [v for v in all_possible_verses if v not in excluded_verses_set]

    if not available_verses:
        raise ValueError("No hay versículos disponibles para seleccionar que no estén ya excluidos. Considera limpiar tu lista de excluidos o añadir más versículos posibles.")

    # Si hay una pista de versículo y no está excluida, usarla
    if main_verse_hint and main_verse_hint in all_possible_verses and main_verse_hint not in excluded_verses_set:
        print(f"INFO: Usando versículo principal sugerido (hint): {main_verse_hint}")
        return main_verse_hint
    elif main_verse_hint:
        print(f"INFO: El versículo principal sugerido '{main_verse_hint}' está excluido o no es válido/no disponible. Seleccionando uno aleatorio.")

    # Si la pista no es válida o está excluida, o no hay pista, seleccionar aleatoriamente
    selected_verse = random.choice(available_verses)
    print(f"INFO: Versículo principal seleccionado aleatoriamente: {selected_verse}")
    return selected_verse


# --- Función para interactuar con Gemini (CON RETRIES) ---
@retry(
    wait=wait_exponential(multiplier=1, min=4, max=10),
    stop=stop_after_attempt(3), # Intentar hasta 3 veces
    retry=retry_if_exception_type(HTTPException) # Reintentar solo si es una HTTPException (u otros errores de red/API)
)
async def generate_devocional_content_gemini(
    current_date: date, lang: str, version: str, verse: str, topic: Optional[str] = None
) -> DevotionalContent:
    """
    Genera el contenido de un devocional usando Google Gemini.
    Ahora recibe el versículo directamente.
    """
    try:
        # MODELO CORREGIDO: gemini-2.0-flash-lite
        model = genai.GenerativeModel('gemini-2.0-flash-lite', generation_config=generation_config_global, safety_settings=safety_settings_global)
        
        # Obtener la versión abreviada del versículo para el prompt (ahorro de tokens)
        abbreviated_verse_for_prompt = get_abbreviated_verse_citation(verse)

        # Construcción del prompt con el versículo directamente
        prompt_parts = [
            f"Eres un generador de devocionales bíblicos experto y devoto. Para la fecha {current_date.strftime('%Y-%m-%d')}, en {lang.upper()}-{version}, genera un devocional basado en el versículo clave: \"{abbreviated_verse_for_prompt}\".",
            "La respuesta debe ser un JSON con las siguientes claves:",
            "- `id`: Un identificador único (ej. juan316RVR1960).",
            "- `date`: La fecha del devocional en formato 'YYYY-MM-DD'.",
            "- `language`: El idioma (ej. 'es', 'en').",
            "- `version`: La versión de la Biblia (ej. 'RVR1960', 'KJV').",
            "- `versiculo`: El versículo completo, incluyendo la versión de la Biblia, la cita exacta y el texto bíblico entre comillas dobles (ej. 'Juan 3:16 RVR1960: \"\"Porque de tal manera amó Dios al mundo...\"\"').",
            "- `reflexion`: Una reflexión profunda y contextualizada sobre el versículo (300 palabras).",
            "- `para_meditar`: Una lista de 3 objetos JSON, donde cada objeto representa un versículo para meditar y tiene las siguientes claves: - cita: La referencia del versículo (ej. 'Filipenses 4:6'), - texto: El texto del versículo (ej. 'Por nada estéis afanosos...').",
            "- `oracion`: Una oración relacionada con el tema del devocional (150 palabras) y siempre finalizar con: en el nombre de Jesús, amén.",
            "- `tags`: Una lista de 2 palabras clave (ej. ['Fe', 'Esperanza'] palabra individual).",
            f"Asegúrate de que la cita del versículo principal en la clave `versiculo` sea idéntica a '{verse}' en su formato completo (Libro Capítulo:Versículo)." # Pedimos el formato completo para la respuesta JSON
        ]
        if topic:
            prompt_parts.append(f"El tema sugerido para el devocional es: {topic}.")
        
        print(f"DEBUG: Enviando prompt a Gemini para versículo (abreviado en prompt): {abbreviated_verse_for_prompt} (Original: {verse}) y fecha: {current_date.strftime('%Y-%m-%d')}")
        # print(f"DEBUG: Prompt completo: {' '.join(prompt_parts)}") # Descomentar para ver el prompt completo

        response = await model.generate_content_async(prompt_parts)
        
        # Asumiendo que la respuesta esperada es un JSON válido
        response_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        devocional_data = json.loads(response_text)

        # Validar si el versículo en la respuesta coincide con el versículo solicitado
        extracted_verse_from_response = extract_verse_from_content(devocional_data.get("versiculo", ""))
        
        # Comparación más robusta, aunque la función extract_verse_from_content ya debería normalizarlo
        if extracted_verse_from_response and extracted_verse_from_response.lower() != verse.lower():
            print(f"ADVERTENCIA: El versículo extraído de la respuesta de Gemini ('{extracted_verse_from_response}') no coincide con el versículo solicitado ('{verse}').")
            # En este punto, si la normalización falla, la advertencia persistirá.
            # Puedes decidir si esto debe ser un error que detenga la generación.
            # Por ahora, es una advertencia.

        print(f"INFO: Devocional generado por Gemini para {verse}.")
        return DevotionalContent(**devocional_data)

    except json.JSONDecodeError as e:
        print(f"ERROR: Fallo al decodificar JSON de la respuesta de Gemini: {e}. Respuesta: {response.text[:500] if 'response' in locals() else 'No hay respuesta.'}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al procesar la respuesta de Gemini: {e}"
        )
    except Exception as e:
        print(f"ERROR: Error al generar devocional con Gemini para {verse}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en la generación de Gemini: {e}"
        )


# --- Ruta de la API ---
@app.post("/generate_devotionals", response_model=ApiResponse)
async def generate_devotionals(request: GenerateRequest):
    response_data = LanguageData()
    current_date = request.start_date
    delta = timedelta(days=1)

    while current_date <= request.end_date:
        date_str = current_date.strftime('%Y-%m-%d')
        
        print(f"\n--- Procesando devocional para el día: {date_str} ---")

        # MASTER VERSION GENERATION
        try:
            # Seleccionar el versículo principal, priorizando el hint si es válido y no excluido
            main_verse = seleccionar_versiculo_para_generacion(excluded_verses, request.main_verse_hint)
            
            # Llamar a Gemini con el versículo ya seleccionado
            master_devocional = await generate_devocional_content_gemini(
                current_date, request.master_lang, request.master_version, main_verse, request.topic
            )
            
            # Solo añadir el versículo a excluidos si la generación maestra fue exitosa
            if main_verse not in excluded_verses: # Evitar añadir duplicados si ya estaba ahí por algún otro motivo
                excluded_verses.add(main_verse)
                print(f"INFO: '{main_verse}' añadido a versículos excluidos.")

            if request.master_lang == "es":
                if date_str not in response_data.es:
                    response_data.es[date_str] = []
                response_data.es[date_str].append(master_devocional)
            elif request.master_lang == "en":
                if date_str not in response_data.en:
                    response_data.en[date_str] = []
                response_data.en[date_str].append(master_devocional)

            # Generar otras versiones (si aplica), usando el mismo versículo maestro
            for lang_to_generate, versions_to_generate in request.other_versions.items():
                if lang_to_generate == "es":
                    if date_str not in response_data.es:
                        response_data.es[date_str] = []
                    current_lang_date_list = response_data.es[date_str]
                elif lang_to_generate == "en":
                    if date_str not in response_data.en:
                        response_data.en[date_str] = []
                    current_lang_date_list = response_data.en[date_str]
                else:
                    continue # Saltar idiomas no soportados explícitamente aquí

                for version_to_generate in versions_to_generate:
                    if not (lang_to_generate == request.master_lang and version_to_generate == request.master_version):
                        try:
                            # Reutilizar el main_verse de la generación maestra
                            other_version_devotional = await generate_devocional_content_gemini(
                                current_date, lang_to_generate, version_to_generate, main_verse, request.topic
                            )
                            current_lang_date_list.append(other_version_devotional)
                            print(f"INFO: Devocional generado para {lang_to_generate}-{version_to_generate} con versículo: {main_verse}")
                        except Exception as e:
                            print(f"ERROR: Fallo al generar devocional para {lang_to_generate}-{version_to_generate} con versículo '{main_verse}': {e}")
                            current_lang_date_list.append(create_error_devocional(
                                current_date, lang_to_generate, version_to_generate, f"Fallo en generación de versión adicional: {str(e)}"
                            ))

        except ValueError as ve:
            # Esto captura el error si seleccionar_versiculo_para_generacion no encuentra un versículo
            error_msg = f"No se pudo seleccionar un versículo para la generación maestra: {str(ve)}"
            print(f"ERROR: {error_msg}")
            # Crear devocional de error para la versión maestra
            if request.master_lang == "es":
                if date_str not in response_data.es: response_data.es[date_str] = []
                response_data.es[date_str].append(create_error_devocional(current_date, request.master_lang, request.master_version, error_msg))
            elif request.master_lang == "en":
                if date_str not in response_data.en: response_data.en[date_str] = []
                response_data.en[date_str].append(create_error_devocional(current_date, request.master_lang, request.master_version, error_msg))
            
            # Crear devocionales de error para otras versiones también
            for lang_to_generate, versions_to_generate in request.other_versions.items():
                if lang_to_generate == "es":
                    if date_str not in response_data.es: response_data.es[date_str] = []
                    current_lang_date_list = response_data.es[date_str]
                elif lang_to_generate == "en":
                    if date_str not in response_data.en: response_data.en[date_str] = []
                    current_lang_date_list = response_data.en[date_str]
                else:
                    continue
                for version_to_generate in versions_to_generate:
                    if not (lang_to_generate == request.master_lang and version_to_generate == request.master_version):
                        current_lang_date_list.append(create_error_devocional(
                            current_date, lang_to_generate, version_to_generate, f"No generado debido a fallo en selección de versículo maestro: {str(ve)}"
                        ))

        except Exception as e:
            # Este es un error más general durante la generación maestra
            print(f"ERROR: Error general al generar la versión maestra para {date_str}: {e}")
            # Crear devocional de error para la versión maestra
            if request.master_lang == "es":
                if date_str not in response_data.es: response_data.es[date_str] = []
                response_data.es[date_str].append(create_error_devocional(current_date, request.master_lang, request.master_version, f"Error inesperado al generar versión maestra: {str(e)}"))
            elif request.master_lang == "en":
                if date_str not in response_data.en: response_data.en[date_str] = []
                response_data.en[date_str].append(create_error_devocional(current_date, request.master_lang, request.master_version, f"Error inesperado al generar versión maestra: {str(e)}"))
            
            # Crear devocionales de error para otras versiones también
            for lang_to_generate, versions_to_generate in request.other_versions.items():
                if lang_to_generate == "es":
                    if date_str not in response_data.es: response_data.es[date_str] = []
                    current_lang_date_list = response_data.es[date_str]
                elif lang_to_generate == "en":
                    if date_str not in response_data.en: response_data.en[date_str] = []
                    current_lang_date_list = response_data.en[date_str]
                else:
                    continue 

                for version_to_generate in versions_to_generate:
                    if not (lang_to_generate == request.master_lang and version_to_generate == request.master_version):
                        current_lang_date_list.append(create_error_devocional(
                            current_date, lang_to_generate, version_to_generate, f"No generado debido a error en versión maestra: {str(e)}"
                        ))

        current_date += delta # Avanza al siguiente día

    save_excluded_verses() # Guarda los versículos excluidos al final de la solicitud
    print(f"DEBUG: Estado final de excluded_verses: {excluded_verses}")
    return ApiResponse(
        status="success",
        message="Devocionales generados correctamente",
        data=response_data
    )
