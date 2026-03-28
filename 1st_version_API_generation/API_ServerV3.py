import traceback
import re


#launch-> uvicorn API_ServerV3:app --host 0.0.0.0 --port 50000 --reload
# --- Guardado progresivo de devocionales ---
PROGRESS_FILE = "devocionales_progress.json"

def save_progress(data):
    try:
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"ERROR al guardar progreso: {e}")

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"ERROR al cargar progreso: {e}")
    return None
# Rollback: Eliminar el diccionario de metadatos de versiones.

# Agregar la versión TCV como string aceptado en la lógica, igual que las otras versiones.
# Ejemplo de uso en la lógica:
# ...existing code...
# Puedes usar "TCV" como valor para master_version o en other_versions, igual que "RVR1960", "KJV", "ARC", "LS1910", "CUVS", "JCB", etc.
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

# MODIFICADO: LanguageData ahora soporta 4 idiomas
class LanguageData(BaseModel):
    es: Dict[str, List[DevotionalContent]] = Field(default_factory=dict)
    en: Dict[str, List[DevotionalContent]] = Field(default_factory=dict)
    pt: Dict[str, List[DevotionalContent]] = Field(default_factory=dict)  # Portugués
    fr: Dict[str, List[DevotionalContent]] = Field(default_factory=dict)  # Francés
    zh: Dict[str, List[DevotionalContent]] = Field(default_factory=dict)  # Chino simplificado
    ja: Dict[str, List[DevotionalContent]] = Field(default_factory=dict)  # Japonés

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
    return { "以弗所书 1:11", "以弗所书 1:7", "以弗所书 2:19-20", "以弗所书 2:4-5", "以弗所书 3:16", "以弗所书 3:18-19", "以弗所书 3:20", "以弗所书 4:1", "以弗所书 4:15", "以弗所书 5:16",
    "以弗所书 5:18", "以弗所书 5:2", "以弗所书 6:10", "以弗所书 6:11", "以弗所书 6:12", "使徒行传 10:34-35", "使徒行传 10:38", "使徒行传 10:43", "使徒行传 13:38-39", "使徒行传 16:30",
    "使徒行传 16:31", "使徒行传 17:11", "使徒行传 1:5", "使徒行传 20:24", "使徒行传 26:18", "使徒行传 2:21", "使徒行传 3:19", "使徒行传 3:26", "使徒行传 4:20", "使徒行传 7:55-56",
    "使徒行传 7:60", "使徒行传 9:6", "加拉太书 1:10", "加拉太书 1:4", "加拉太书 2:16", "加拉太书 2:2", "加拉太书 3:11", "加拉太书 3:22", "加拉太书 3:26", "加拉太书 4:19",
    "加拉太书 4:5", "加拉太书 4:7", "加拉太书 5:16", "加拉太书 5:25", "加拉太书 5:6", "加拉太书 6:1", "加拉太书 6:10", "加拉太书 6:8", "启示录 1:7", "启示录 1:8",
    "启示录 20:6", "启示录 22:12-13", "启示录 22:7", "启示录 7:12", "启示录 7:9-10", "哥林多前书 10:23", "哥林多前书 11:23-25", "哥林多前书 11:26", "哥林多前书 11:28", "哥林多前书 12:1",
    "哥林多前书 13:1", "哥林多前书 13:7", "哥林多前书 14:1", "哥林多前书 14:26", "哥林多前书 14:33", "哥林多前书 15:1", "哥林多前书 15:20", "哥林多前书 15:35", "哥林多前书 1:2", "哥林多前书 1:25",
    "哥林多前书 1:30", "哥林多前书 2:16", "哥林多前书 2:4", "哥林多前书 2:9", "哥林多前书 3:11", "哥林多前书 3:13", "哥林多前书 3:18", "哥林多前书 4:20", "哥林多前书 4:7", "哥林多前书 4:8",
    "哥林多前书 5:7", "哥林多前书 6:19", "哥林多前书 7:17", "哥林多前书 7:23", "哥林多前书 7:31", "哥林多前书 8:1", "哥林多前书 8:6", "哥林多前书 9:22", "哥林多前书 9:27", "哥林多后书 10:4-5",
    "哥林多后书 11:14", "哥林多后书 11:3", "哥林多后书 12:10", "哥林多后书 12:9", "哥林多后书 13:4", "哥林多后书 1:20", "哥林多后书 1:9", "哥林多后书 3:17", "哥林多后书 3:18", "哥林多后书 3:6",
    "哥林多后书 4:16", "哥林多后书 4:17", "哥林多后书 4:6", "哥林多后书 5:1", "哥林多后书 5:14", "哥林多后书 5:7", "哥林多后书 6:14", "哥林多后书 6:16", "哥林多后书 8:12", "哥林多后书 8:7",
    "哥林多后书 8:9", "哥林多后书 9:10", "哥林多后书 9:15", "希伯来书 10:14", "希伯来书 10:19-22", "希伯来书 11:3", "希伯来书 12:11", "希伯来书 12:7", "希伯来书 13:5", "希伯来书 13:8",
    "希伯来书 2:1", "希伯来书 2:17-18", "希伯来书 2:9", "希伯来书 3:1", "希伯来书 3:13", "希伯来书 3:6", "希伯来书 4:12", "希伯来书 4:14", "希伯来书 5:8-9", "希伯来书 6:1",
    "希伯来书 7:25", "希伯来书 8:6", "希伯来书 9:27-28", "帖撒罗尼迦前书 1:5", "帖撒罗尼迦前书 1:9-10", "帖撒罗尼迦前书 2:19", "帖撒罗尼迦前书 2:4", "帖撒罗尼迦前书 3:12", "帖撒罗尼迦前书 4:1", "帖撒罗尼迦前书 4:3",
    "帖撒罗尼迦前书 5:11", "帖撒罗尼迦前书 5:22", "帖撒罗尼迦前书 5:5", "帖撒罗尼迦后书 1:3", "帖撒罗尼迦后书 2:1-2", "帖撒罗尼迦后书 3:1", "帖撒罗尼迦后书 3:16", "彼得前书 1:15-16", "彼得前书 1:22", "彼得前书 1:8-9",
    "彼得前书 2:2", "彼得前书 2:21", "彼得前书 3:14", "彼得前书 3:18", "彼得前书 3:9", "彼得前书 4:8", "彼得前书 5:8", "彼得后书 1:5-7", "彼得后书 1:8", "彼得后书 2:9",
    "彼得后书 3:13", "彼得后书 3:18", "彼得后书 3:9", "提多书 2:11-14", "提多书 3:5", "提摩太前书 1:16", "提摩太前书 1:5", "提摩太前书 3:16", "提摩太前书 3:9", "提摩太前书 4:12",
    "提摩太前书 4:8", "提摩太前书 5:8", "提摩太前书 6:11", "提摩太前书 6:6", "提摩太后书 1:12", "提摩太后书 1:9", "提摩太后书 2:13", "提摩太后书 2:3-4", "提摩太后书 3:1-5", "提摩太后书 3:16",
    "提摩太后书 3:17", "提摩太后书 4:2", "提摩太后书 4:7-8", "歌罗西书 1:18", "歌罗西书 1:27", "歌罗西书 2:10", "歌罗西书 2:9", "歌罗西书 3:10", "歌罗西书 3:17", "犹大书 1:20-21",
    "约翰一书 1:7", "约翰一书 1:8", "约翰一书 2:6", "约翰一书 3:16", "约翰一书 3:23", "约翰一书 4:1", "约翰一书 4:16", "约翰一书 4:18", "约翰一书 4:7", "约翰一书 5:14",
    "约翰一书 5:18", "约翰一书 5:4", "约翰三书 1:11", "约翰二书 1:6", "约翰福音 10:11", "约翰福音 10:27-28", "约翰福音 11:25-26", "约翰福音 11:40", "约翰福音 12:24", "约翰福音 12:47",
    "约翰福音 14:12", "约翰福音 14:15", "约翰福音 14:21", "约翰福音 15:13", "约翰福音 15:16", "约翰福音 16:7-8", "约翰福音 17:17", "约翰福音 17:20-21", "约翰福音 17:3", "约翰福音 19:30",
    "约翰福音 1:14", "约翰福音 1:29", "约翰福音 1:4", "约翰福音 2:5", "约翰福音 3:17", "约翰福音 3:3", "约翰福音 3:30", "约翰福音 4:14", "约翰福音 5:24", "约翰福音 5:30",
    "约翰福音 5:45", "约翰福音 6:27", "约翰福音 6:40", "约翰福音 6:51", "约翰福音 7:17", "约翰福音 8:12", "约翰福音 8:36", "约翰福音 9:4", "罗马书 11:25-26", "罗马书 11:29",
    "罗马书 11:6", "罗马书 12:10", "罗马书 12:14", "罗马书 12:15", "罗马书 13:1", "罗马书 13:9", "罗马书 14:10", "罗马书 14:17", "罗马书 14:23", "罗马书 15:4",
    "罗马书 1:17", "罗马书 2:13", "罗马书 3:20", "罗马书 3:28", "罗马书 4:13", "罗马书 4:3", "罗马书 4:5", "罗马书 5:12", "罗马书 5:17", "罗马书 5:5",
    "罗马书 6:14", "罗马书 6:17-18", "罗马书 6:4", "罗马书 7:14", "罗马书 8:1", "罗马书 8:6", "罗马书 9:15-16", "罗马书 9:28", "罗马书 9:33", "腓立比书 1:21",
    "腓立比书 1:27", "腓立比书 2:14", "腓立比书 2:3", "腓立比书 2:5", "腓立比书 3:10", "腓立比书 3:14", "腓立比书 3:20", "腓立比书 4:6", "腓立比书 4:7", "路加福音 11:28",
    "路加福音 11:9-10", "路加福音 12:31", "路加福音 13:24", "路加福音 13:30", "路加福音 14:11", "路加福音 16:10", "路加福音 16:13", "路加福音 17:5", "路加福音 18:14", "路加福音 18:27",
    "路加福音 19:10", "路加福音 1:37", "路加福音 1:45", "路加福音 20:25", "路加福音 21:36", "路加福音 22:19-20", "路加福音 22:42", "路加福音 23:34", "路加福音 24:49", "路加福音 2:10-11",
    "路加福音 4:18-19", "路加福音 4:4", "路加福音 4:43", "路加福音 5:24", "路加福音 5:32", "路加福音 6:27-28", "路加福音 6:46-47", "路加福音 7:50", "路加福音 8:15", "路加福音 8:21",
    "路加福音 9:24", "雅各书 1:12", "雅各书 1:2-4", "雅各书 1:6", "雅各书 2:26", "雅各书 2:8", "雅各书 3:17", "雅各书 3:2", "雅各书 4:2", "雅各书 5:16",
    "马可福音 10:27", "马可福音 10:45", "马可福音 11:24", "马可福音 12:30-31", "马可福音 14:36", "马可福音 14:38", "马可福音 15:39", "马可福音 1:17", "马可福音 2:17", "马可福音 3:35",
    "马可福音 4:20", "马可福音 5:36", "马可福音 6:34", "马可福音 7:23", "马可福音 8:36", "马可福音 9:23", "马可福音 9:35", "马太福音 10:16", "马太福音 10:32", "马太福音 11:29",
    "马太福音 12:20", "马太福音 12:31", "马太福音 13:23", "马太福音 14:14", "马太福音 15:19", "马太福音 17:20", "马太福音 17:5", "马太福音 18:3", "马太福音 19:26", "马太福音 20:28",
    "马太福音 21:22", "马太福音 21:42", "马太福音 22:21", "马太福音 24:13", "马太福音 25:21", "马太福音 26:39", "马太福音 26:41", "马太福音 3:11", "马太福音 4:19", "马太福音 5:13",
    "马太福音 5:44", "马太福音 6:6", "马太福音 7:1", "马太福音 8:17", "马太福音 9:12"



}


def get_abbreviated_verse_citation(full_verse_citation: str) -> str:
    """
    Convierte una cita de versículo con nombre de libro completo a su acrónimo.
    Ej: "Juan 3:16" -> "Jn 3:16"
    """
    # Maneja libros con números (ej. "1 Juan", "2 Corintios")
    # Busca el primer dígito si el primer elemento es un número, o el primer espacio
    match = re.match(r'(\d?\s*[A-ZÁÉÍÓÚÜÑ a-záéíóüñ]+\s?\d*)\s*(.*)', full_verse_citation)
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
    match = re.search(
        r'((?:[123]\s)?[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u3400-\u4DBFa-zA-ZÀ-ÿ]+(?:\s+[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u3400-\u4DBFa-zA-ZÀ-ÿ]+)*)\s+(\d+:\d+(?:-\d+)?)',
        content,
        re.IGNORECASE
    )

    if not match:
        print(f"DEBUG: No se pudo extraer el patrón de versículo del contenido: {content[:100]}...")
        return None

    book_raw = match.group(1).strip() # Ej. "1 Corintios" o "Corintios" o "Juan"
    chapter_verse_raw = match.group(2).strip() # Ej. "10:13" o "5:16-18"

    # Tu lista de nombres canónicos de libros del Nuevo Testamento (de obtener_todos_los_versiculos_posibles)
    # la usaremos para encontrar el nombre exacto.
    canonical_nt_books = {
        "Mateo", "Marcos", "Lucas", "Juan", "Hechos", "Romanos",
        "1 Corintios", "2 Corintios", "Gálatas", "Efesios", "Filipenses", "Colossenses",
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


# MODIFICADO: Función auxiliar para asignar devocional al idioma correcto
def assign_devotional_to_language(response_data: LanguageData, lang: str, date_str: str, devotional: DevotionalContent):
    """Asigna un devocional al idioma correcto en response_data"""
    if lang == "es":
        if date_str not in response_data.es:
            response_data.es[date_str] = []
        response_data.es[date_str].append(devotional)
    elif lang == "en":
        if date_str not in response_data.en:
            response_data.en[date_str] = []
        response_data.en[date_str].append(devotional)
    elif lang == "pt":
        if date_str not in response_data.pt:
            response_data.pt[date_str] = []
        response_data.pt[date_str].append(devotional)
    elif lang == "fr":
        if date_str not in response_data.fr:
            response_data.fr[date_str] = []
        response_data.fr[date_str].append(devotional)
    elif lang == "zh":
        if date_str not in response_data.zh:
            response_data.zh[date_str] = []
        response_data.zh[date_str].append(devotional)
    elif lang == "ja":
        if date_str not in response_data.ja:
            response_data.ja[date_str] = []
        response_data.ja[date_str].append(devotional)
    else:
        print(f"ADVERTENCIA: Idioma '{lang}' no soportado. Devocional no asignado.")

# MODIFICADO: Función auxiliar para obtener lista de devocionales por idioma
def get_language_devotionals_list(response_data: LanguageData, lang: str, date_str: str) -> List[DevotionalContent]:
    """Obtiene la lista de devocionales para un idioma específico"""
    if lang == "es":
        if date_str not in response_data.es:
            response_data.es[date_str] = []
        return response_data.es[date_str]
    elif lang == "en":
        if date_str not in response_data.en:
            response_data.en[date_str] = []
        return response_data.en[date_str]
    elif lang == "pt":
        if date_str not in response_data.pt:
            response_data.pt[date_str] = []
        return response_data.pt[date_str]
    elif lang == "fr":
        if date_str not in response_data.fr:
            response_data.fr[date_str] = []
        return response_data.fr[date_str]
    elif lang == "zh":
        if date_str not in response_data.zh:
            response_data.zh[date_str] = []
        return response_data.zh[date_str]
    elif lang == "ja":
        if date_str not in response_data.ja:
            response_data.ja[date_str] = []
        return response_data.ja[date_str]
    else:
        print(f"ADVERTENCIA: Idioma '{lang}' no soportado. Retornando lista vacía.")
        return []

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
    "- `language`: El idioma (ej. 'es', 'en', 'pt', 'fr', 'zh', 'ja').",
    "- `version`: La versión de la Biblia (ej. 'RVR1960', 'KJV', 'ARC', 'LS1910').",
    "",
    "=== FORMATO CRÍTICO PARA EL CAMPO 'versiculo' ===",
    f"El campo 'versiculo' debe seguir EXACTAMENTE este formato sin excepciones:",
    f"\"{verse} {version}: \\\"<texto completo del versículo>\\\"\"",
    "2. NO usar solo el último número, si el versículo es un rango (ej: 2:8-9) debes incluir todos los versículos del rango",
    "",
    "=== CONTINUACIÓN DE LOS CAMPOS ===",
    "- `reflexion`: Una reflexión profunda y contextualizada sobre el versículo (300 palabras).",
    "- `para_meditar`: Una lista de 3 objetos JSON, donde cada objeto representa un versículo de la misma versión bíblica para meditar y tiene las siguientes claves: - cita: La referencia del versículo (ej. 'Filipenses 4:6'), - texto: El texto del versículo (ej. 'Por nada estéis afanosos...').",
    "- `oracion`: Una oración relacionada con el tema del devocional (150 palabras solo en el idioma lang_to_generate). DEBE finalizar con la frase 'en el nombre de Jesús, amén' traducida correctamente al idioma de generación (lang_to_generate).",
    "- `tags`: Una lista de 2 palabras clave (ej. ['Fe', 'Esperanza'] palabra individual).",
    "",
    "=== CONTROL DE CALIDAD ===",
    "CRÍTICO - Antes de entregar tu respuesta, verifica:tod el texto debe ser solo en el lang_to_generate no en otros idiomas",
    "1. NO repitas palabras o frases consecutivamente (ej: 'nosotros nosotros', '私たち私たち')",
    "2. NO dupliques párrafos o secciones de texto",
    "3. La oración DEBE terminar EXACTAMENTE con la frase 'en el nombre de Jesús, amén' en el idioma correcto",
    "4. Todos los versículos de 'para_meditar' deben ser de la misma versión bíblica especificada",
            
            
]
        
        if topic:
            prompt_parts.append(f"El tema sugerido para el devocional es: {topic}.")
        
        # Refuerzo adicional para rangos de versículos
        if '-' in verse and ':' in verse:  # Detectar si es un rango (ej: "Juan 3:16-18")
            prompt_parts.append("")
            prompt_parts.append("⚠️ ADVERTENCIA IMPORTANTE: Este versículo contiene un RANGO.")
            prompt_parts.append(f"Debes incluir el texto COMPLETO de todos los versículos desde el inicio hasta el final del rango.")
            prompt_parts.append(f"El campo 'versiculo' DEBE comenzar con: \"{verse} {version}:\"")
            prompt_parts.append("NO uses solo el último número del rango.")
        
        print(f"DEBUG: Enviando prompt a Gemini para versículo (abreviado en prompt): {abbreviated_verse_for_prompt} (Original: {verse}) y fecha: {current_date.strftime('%Y-%m-%d')}")
        # print(f"DEBUG: Prompt completo: {' '.join(prompt_parts)}") # Descomentar para ver el prompt completo

        response = await model.generate_content_async(prompt_parts)
        
        # Asumiendo que la respuesta esperada es un JSON válido
        response_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        devocional_data = json.loads(response_text)

        # Validar si el versículo en la respuesta coincide con el versículo solicitado
        extracted_verse_from_response = extract_verse_from_content(devocional_data.get("versiculo", ""))
        
        # Comparación más robusta
        if extracted_verse_from_response and extracted_verse_from_response.lower() != verse.lower():
            print(f"ADVERTENCIA: El versículo extraído de la respuesta de Gemini ('{extracted_verse_from_response}') no coincide con el versículo solicitado ('{verse}').")

        print(f"INFO: Devocional generado por Gemini para {verse}.")
        
        # Sobrescribir el campo 'id' para que sea siempre '<VERSION>-<YYYYMMDD>'
        devocional_data['id'] = f"{version}-{current_date.strftime('%Y%m%d')}"
        
        # Asegurar que 'version' solo contenga el valor del parámetro 'version' (el prompt)
        devocional_data['version'] = version
        
        # --- RECONSTRUCCIÓN ROBUSTA DEL VERSÍCULO PRINCIPAL ---
        if 'versiculo' in devocional_data:
            versiculo_raw = devocional_data['versiculo']
            
            # Intentar limpiar el versículo de prefijos basura comunes
            # Patrón 1: Quitar números sueltos al inicio (ej: "18: ", "9 ", "30 ")
            versiculo_raw = re.sub(r'^\d+\s*:\s*', '', versiculo_raw)
            
            # Patrón 2: Quitar prefijos como "JA-VERSION" (ej: "9 JA-新改訳2003:")
            versiculo_raw = re.sub(rf'^\d+\s+(?:JA-)?{re.escape(version)}\s*:\s*', '', versiculo_raw)
            
            # Patrón 3: Quitar duplicación de versión
            versiculo_raw = re.sub(rf'{re.escape(version)}\s+{re.escape(version)}', version, versiculo_raw)
            
            # Buscar el texto después de '<verse> <version>:' 
            pattern = rf'{re.escape(verse)}\s+{re.escape(version)}\s*:\s*[\"「""]?(.+?)[\"」""]?$'
            match = re.search(pattern, versiculo_raw)
            
            if match:
                clean_text = match.group(1).strip()
            else:
                # Si no se encuentra el patrón exacto, intentar limpiar basura conocida
                parts = versiculo_raw.split(f'{version}:', 1)
                if len(parts) > 1:
                    clean_text = parts[1].strip().strip('"「」""')
                else:
                    # Último recurso: limpiar todo lo que esté antes de las comillas
                    clean_text = re.sub(r'^[^"「""]*[\"「""]', '', versiculo_raw, 1).strip().strip('"「」""')
            
            # Reconstruir el versículo en el formato correcto
            devocional_data['versiculo'] = f"{verse} {version}: \"{clean_text}\""
        
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
    delta = timedelta(days=1)

    # Cargar progreso si existe
    progress = load_progress()
    if progress:
        print("INFO: Progreso previo encontrado. Retomando desde el último guardado.")
        # Reconstruir response_data desde progreso
        try:
            response_data = LanguageData.parse_obj(progress["response_data"])
            current_date = date.fromisoformat(progress["current_date"])
        except Exception as e:
            print(f"ERROR al reconstruir progreso: {e}")
            response_data = LanguageData()
            current_date = request.start_date
    else:
        current_date = request.start_date

    while current_date <= request.end_date:
        date_str = current_date.strftime('%Y-%m-%d')
        print(f"\n--- Procesando devocional para el día: {date_str} ---")
        try:
            main_verse = seleccionar_versiculo_para_generacion(excluded_verses, request.main_verse_hint)
            master_devocional = await generate_devocional_content_gemini(
                current_date, request.master_lang, request.master_version, main_verse, request.topic
            )
            if main_verse not in excluded_verses:
                excluded_verses.add(main_verse)
                print(f"INFO: '{main_verse}' añadido a versículos excluidos.")
            assign_devotional_to_language(response_data, request.master_lang, date_str, master_devocional)
            for lang_to_generate, versions_to_generate in request.other_versions.items():
                current_lang_date_list = get_language_devotionals_list(response_data, lang_to_generate, date_str)
                for version_to_generate in versions_to_generate:
                    if not (lang_to_generate == request.master_lang and version_to_generate == request.master_version):
                        try:
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
            error_msg = f"No se pudo seleccionar un versículo para la generación maestra: {str(ve)}"
            print(f"ERROR: {error_msg}")
            assign_devotional_to_language(response_data, request.master_lang, date_str, 
                                        create_error_devocional(current_date, request.master_lang, request.master_version, error_msg))
            for lang_to_generate, versions_to_generate in request.other_versions.items():
                current_lang_date_list = get_language_devotionals_list(response_data, lang_to_generate, date_str)
                for version_to_generate in versions_to_generate:
                    if not (lang_to_generate == request.master_lang and version_to_generate == request.master_version):
                        current_lang_date_list.append(create_error_devocional(
                            current_date, lang_to_generate, version_to_generate, f"No generado debido a fallo en selección de versículo maestro: {str(ve)}"
                        ))
        except Exception as e:
            print(f"ERROR: Error general al generar la versión maestra para {date_str}: {e}\n{traceback.format_exc()}")
            assign_devotional_to_language(response_data, request.master_lang, date_str,
                                        create_error_devocional(current_date, request.master_lang, request.master_version, f"Error inesperado al generar versión maestra: {str(e)}"))
            for lang_to_generate, versions_to_generate in request.other_versions.items():
                current_lang_date_list = get_language_devotionals_list(response_data, lang_to_generate, date_str)
                for version_to_generate in versions_to_generate:
                    if not (lang_to_generate == request.master_lang and version_to_generate == request.master_version):
                        current_lang_date_list.append(create_error_devocional(
                            current_date, lang_to_generate, version_to_generate, f"No generado debido a error en versión maestra: {str(e)}"
                        ))

        # Guardado progresivo después de cada día
        save_progress({
            "response_data": response_data.dict(),
            "current_date": (current_date + delta).isoformat()
        })
        current_date += delta

    save_excluded_verses()
    print(f"DEBUG: Estado final de excluded_verses: {excluded_verses}")
    # Eliminar archivo de progreso al finalizar correctamente
    if os.path.exists(PROGRESS_FILE):
        try:
            os.remove(PROGRESS_FILE)
        except Exception as e:
            print(f"ERROR al eliminar archivo de progreso: {e}")
    return ApiResponse(
        status="success",
        message="Devocionales generados correctamente",
        data=response_data
    )
