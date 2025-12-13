import traceback
import re
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
    return { "エペソ人への手紙 1:13-14", "エペソ人への手紙 1:3-4", "エペソ人への手紙 1:7", "エペソ人への手紙 2:10", "エペソ人への手紙 2:19-22", "エペソ人への手紙 2:4-5", "エペソ人への手紙 2:8-9", "エペソ人への手紙 4:22-24", "エペソ人への手紙 4:26-27", "エペソ人への手紙 4:29",
    "エペソ人への手紙 4:30", "エペソ人への手紙 4:31", "エペソ人への手紙 5:1-2", "エペソ人への手紙 5:18", "エペソ人への手紙 5:25-27", "エペソ人への手紙 6:1-3", "エペソ人への手紙 6:10-13", "エペソ人への手紙 6:19-20", "ガラテヤ人への手紙 2:20", "ガラテヤ人への手紙 5:1",
    "ガラテヤ人への手紙 5:13", "ガラテヤ人への手紙 5:16", "ガラテヤ人への手紙 5:22-23", "ガラテヤ人への手紙 6:14", "ガラテヤ人への手紙 6:2", "ガラテヤ人への手紙 6:7-8", "ガラテヤ人への手紙 6:9", "コリント人への手紙第一 10:31", "コリント人への手紙第一 13:1-3", "コリント人への手紙第一 13:4-7",
    "コリント人への手紙第一 14:33", "コリント人への手紙第一 15:3-4", "コリント人への手紙第一 15:57", "コリント人への手紙第一 16:14", "コリント人への手紙第一 1:18", "コリント人への手紙第一 2:9", "コリント人への手紙第一 3:11", "コリント人への手紙第一 3:16", "コリント人への手紙第一 4:2", "コリント人への手紙第一 7:23",
    "コリント人への手紙第一 9:24", "コリント人への手紙第二 12:9-10", "コリント人への手紙第二 13:5", "コリント人への手紙第二 1:3-4", "コリント人への手紙第二 3:17", "コリント人への手紙第二 4:16-18", "コリント人への手紙第二 4:5", "コリント人への手紙第二 4:6", "コリント人への手紙第二 4:7", "コリント人への手紙第二 5:17",
    "コリント人への手紙第二 5:18-19", "コリント人への手紙第二 5:20", "コリント人への手紙第二 5:21", "コリント人への手紙第二 5:7", "コリント人への手紙第二 6:14", "コリント人への手紙第二 7:10", "コリント人への手紙第二 8:9", "コリント人への手紙第二 9:7", "コロサイ人への手紙 1:15-17", "コロサイ人への手紙 1:18",
    "コロサイ人への手紙 1:27", "コロサイ人への手紙 2:8", "コロサイ人への手紙 3:1-2", "コロサイ人への手紙 3:12-14", "コロサイ人への手紙 3:15", "コロサイ人への手紙 3:16", "コロサイ人への手紙 3:23", "コロサイ人への手紙 3:5", "コロサイ人への手紙 4:2", "コロサイ人への手紙 4:6",
    "テサロニケ人への手紙第一 1:9-10", "テサロニケ人への手紙第一 4:3-5", "テサロニケ人への手紙第一 5:11", "テサロニケ人への手紙第一 5:19", "テサロニケ人への手紙第一 5:21-22", "テサロニケ人への手紙第一 5:23-24", "テサロニケ人への手紙第二 2:13-14", "テサロニケ人への手紙第二 3:10", "テトスへの手紙 1:5-9", "テトスへの手紙 2:11-14",
    "テトスへの手紙 3:4-5", "テトスへの手紙 3:8", "テモテへの手紙第一 1:15-16", "テモテへの手紙第一 2:8", "テモテへの手紙第一 3:16", "テモテへの手紙第一 4:12", "テモテへの手紙第一 4:7-8", "テモテへの手紙第一 5:8", "テモテへの手紙第一 6:10", "テモテへの手紙第一 6:12",
    "テモテへの手紙第一 6:17-19", "テモテへの手紙第一 6:6-8", "テモテへの手紙第二 1:7", "テモテへの手紙第二 1:9", "テモテへの手紙第二 2:15", "テモテへの手紙第二 2:3-4", "テモテへの手紙第二 3:1-5", "テモテへの手紙第二 3:16", "テモテへの手紙第二 3:17", "テモテへの手紙第二 4:18",
    "テモテへの手紙第二 4:2", "テモテへの手紙第二 4:7-8", "ピリピ人への手紙 1:6", "ピリピ人への手紙 2:14-15", "ピリピ人への手紙 2:3-4", "ピリピ人への手紙 2:5", "ピリピ人への手紙 2:6-8", "ピリピ人への手紙 2:9-11", "ピリピ人への手紙 3:13-14", "ピリピ人への手紙 3:20-21",
    "ピリピ人への手紙 3:7-8", "ピリピ人への手紙 4:13", "ピリピ人への手紙 4:19", "ピリピ人への手紙 4:4", "ピリピ人への手紙 4:6-7", "ピリピ人への手紙 4:8", "ピレモンへの手紙 1:16", "ピレモンへの手紙 1:6", "ヘブル人への手紙 10:24-25", "ヘブル人への手紙 11:1",
    "ヘブル人への手紙 12:1", "ヘブル人への手紙 12:14", "ヘブル人への手紙 12:2", "ヘブル人への手紙 12:6-7", "ヘブル人への手紙 13:14", "ヘブル人への手紙 13:15-16", "ヘブル人への手紙 13:17", "ヘブル人への手紙 13:5", "ヘブル人への手紙 1:3", "ヘブル人への手紙 2:14-15",
    "ヘブル人への手紙 4:12", "ヘブル人への手紙 4:14-16", "ヘブル人への手紙 5:8-9", "ヘブル人への手紙 6:1-2", "ヘブル人への手紙 7:25", "ヘブル人への手紙 8:6", "ヘブル人への手紙 9:27-28", "ペテロの手紙第一 1:3-5", "ペテロの手紙第一 1:8-9", "ペテロの手紙第一 2:2-3",
    "ペテロの手紙第一 2:9-10", "ペテロの手紙第一 3:15", "ペテロの手紙第一 3:8-9", "ペテロの手紙第一 4:8", "ペテロの手紙第一 5:10", "ペテロの手紙第一 5:5-6", "ペテロの手紙第一 5:7", "ペテロの手紙第一 5:8-9", "ペテロの手紙第二 1:19-21", "ペテロの手紙第二 1:3-4",
    "ペテロの手紙第二 1:5-7", "ペテロの手紙第二 3:10", "ペテロの手紙第二 3:9", "マタイの福音書 10:32-33", "マタイの福音書 10:37-39", "マタイの福音書 11:28-30", "マタイの福音書 12:31-32", "マタイの福音書 13:3-9", "マタイの福音書 13:31-32", "マタイの福音書 13:44-46",
    "マタイの福音書 14:19-21", "マタイの福音書 16:18-19", "マタイの福音書 16:24-26", "マタイの福音書 18:20", "マタイの福音書 18:21-22", "マタイの福音書 19:26", "マタイの福音書 1:1", "マタイの福音書 20:26-28", "マタイの福音書 21:21-22", "マタイの福音書 23:11-12",
    "マタイの福音書 25:31-36", "マタイの福音書 26:26-28", "マタイの福音書 27:46", "マタイの福音書 2:1-2", "マタイの福音書 3:16-17", "マタイの福音書 4:19", "マタイの福音書 4:4", "マタイの福音書 5:14-16", "マタイの福音書 5:3-10", "マタイの福音書 5:44-45",
    "マタイの福音書 6:24", "マタイの福音書 6:33", "マタイの福音書 6:9-13", "マタイの福音書 7:12", "マタイの福音書 7:13-14", "マタイの福音書 7:24-27", "マタイの福音書 7:7-8", "マタイの福音書 8:26-27", "マタイの福音書 9:12-13", "マルコの福音書 10:45",
    "マルコの福音書 11:24", "マルコの福音書 12:30-31", "マルコの福音書 14:36", "マルコの福音書 15:39", "マルコの福音書 16:15", "マルコの福音書 1:15", "マルコの福音書 2:17", "マルコの福音書 4:35", "マルコの福音書 6:31", "マルコの福音書 7:20-23",
    "マルコの福音書 8:34", "マルコの福音書 9:23", "マルコの福音書 9:35", "ヤコブの手紙 1:12", "ヤコブの手紙 1:19-20", "ヤコブの手紙 1:2-4", "ヤコブの手紙 1:22", "ヤコブの手紙 1:5", "ヤコブの手紙 2:17", "ヤコブの手紙 2:19",
    "ヤコブの手紙 2:8", "ヤコブの手紙 3:17", "ヤコブの手紙 4:10", "ヤコブの手紙 4:14", "ヤコブの手紙 4:8", "ヤコブの手紙 5:15-16", "ユダの手紙 1:20-21", "ユダの手紙 1:24-25", "ヨハネの手紙第一 1:5-7", "ヨハネの手紙第一 1:8",
    "ヨハネの手紙第一 2:15-17", "ヨハネの手紙第一 2:3-4", "ヨハネの手紙第一 3:1", "ヨハネの手紙第一 3:16", "ヨハネの手紙第一 3:18", "ヨハネの手紙第一 3:2", "ヨハネの手紙第一 3:8", "ヨハネの手紙第一 4:1", "ヨハネの手紙第一 4:11", "ヨハネの手紙第一 4:12",
    "ヨハネの手紙第一 4:16", "ヨハネの手紙第一 4:18", "ヨハネの手紙第一 4:19", "ヨハネの手紙第一 4:9-10", "ヨハネの手紙第一 5:11-12", "ヨハネの手紙第一 5:13", "ヨハネの手紙第一 5:14-15", "ヨハネの手紙第一 5:20", "ヨハネの手紙第一 5:3", "ヨハネの手紙第一 5:4",
    "ヨハネの手紙第三 1:11", "ヨハネの手紙第三 1:4", "ヨハネの手紙第二 1:6", "ヨハネの福音書 10:10", "ヨハネの福音書 11:25-26", "ヨハネの福音書 12:24", "ヨハネの福音書 13:34-35", "ヨハネの福音書 14:1", "ヨハネの福音書 14:15", "ヨハネの福音書 14:16-17",
    "ヨハネの福音書 14:2-3", "ヨハネの福音書 14:27", "ヨハネの福音書 14:6", "ヨハネの福音書 15:1", "ヨハネの福音書 15:12", "ヨハネの福音書 15:13", "ヨハネの福音書 15:16", "ヨハネの福音書 15:5", "ヨハネの福音書 15:8", "ヨハネの福音書 16:33",
    "ヨハネの福音書 16:7", "ヨハネの福音書 17:3", "ヨハネの福音書 1:1", "ヨハネの福音書 1:12", "ヨハネの福音書 20:29", "ヨハネの福音書 21:17", "ヨハネの福音書 3:16", "ヨハネの福音書 3:17", "ヨハネの福音書 3:18", "ヨハネの福音書 3:19",
    "ヨハネの福音書 3:3", "ヨハネの福音書 3:30", "ヨハネの福音書 4:23-24", "ヨハネの福音書 4:24", "ヨハネの福音書 5:24", "ヨハネの福音書 6:35", "ヨハネの福音書 7:38-39", "ヨハネの福音書 8:12", "ヨハネの福音書 8:32", "ヨハネの黙示録 14:13",
    "ヨハネの黙示録 19:11", "ヨハネの黙示録 1:7", "ヨハネの黙示録 1:8", "ヨハネの黙示録 21:1", "ヨハネの黙示録 21:4", "ヨハネの黙示録 22:12-13", "ヨハネの黙示録 22:17", "ヨハネの黙示録 22:20", "ヨハネの黙示録 2:4", "ヨハネの黙示録 2:7",
    "ヨハネの黙示録 3:16", "ヨハネの黙示録 3:20", "ヨハネの黙示録 3:8", "ヨハネの黙示録 4:8", "ヨハネの黙示録 7:16-17", "ルカの福音書 10:19", "ルカの福音書 10:2", "ルカの福音書 11:13", "ルカの福音書 11:28", "ルカの福音書 12:34",
    "ルカの福音書 12:4-5", "ルカの福音書 12:48", "ルカの福音書 14:11", "ルカの福音書 15:10", "ルカの福音書 15:32", "ルカの福音書 15:7", "ルカの福音書 17:20-21", "ルカの福音書 17:6", "ルカの福音書 18:14", "ルカの福音書 18:27",
    "ルカの福音書 19:10", "ルカの福音書 1:37", "ルカの福音書 24:47", "ルカの福音書 24:49", "ルカの福音書 24:6-7", "ルカの福音書 2:10-11", "ルカの福音書 4:18-19", "ルカの福音書 5:32", "ルカの福音書 6:36", "ルカの福音書 6:37-38",
    "ルカの福音書 9:23-24", "ローマ人への手紙 10:13", "ローマ人への手紙 10:4", "ローマ人への手紙 10:9-10", "ローマ人への手紙 11:33-36", "ローマ人への手紙 12:1-2", "ローマ人への手紙 12:12", "ローマ人への手紙 12:3", "ローマ人への手紙 13:1", "ローマ人への手紙 13:8",
    "ローマ人への手紙 14:12", "ローマ人への手紙 15:13", "ローマ人への手紙 15:4", "ローマ人への手紙 1:16", "ローマ人への手紙 1:20", "ローマ人への手紙 3:23", "ローマ人への手紙 3:24", "ローマ人への手紙 3:28", "ローマ人への手紙 5:1", "ローマ人への手紙 5:5",
    "ローマ人への手紙 5:8", "ローマ人への手紙 6:11", "ローマ人への手紙 6:23", "ローマ人への手紙 6:4", "ローマ人への手紙 8:11", "ローマ人への手紙 8:14", "ローマ人への手紙 8:28", "ローマ人への手紙 8:31", "ローマ人への手紙 8:37-39", "ローマ人への手紙 9:15-16",
    "使徒の働き 10:43", "使徒の働き 13:38-39", "使徒の働き 16:31", "使徒の働き 17:11", "使徒の働き 17:28", "使徒の働き 17:30-31", "使徒の働き 1:8", "使徒の働き 20:24", "使徒の働き 20:35", "使徒の働き 2:1-4",
    "使徒の働き 2:21", "使徒の働き 4:12", "使徒の働き 5:29", "使徒の働き 7:55-56", "使徒の働き 9:3-6"



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
            "- `versiculo`: El versículo completo, incluyendo la versión de la Biblia (sin prefijos), la cita exacta y el texto bíblico entre comillas dobles (ej. 'Juan 3:16 RVR1960: \"\"Porque de tal manera amó Dios al mundo...\"\"').",
            "- `reflexion`: Una reflexión profunda y contextualizada sobre el versículo (300 palabras).",
            "- `para_meditar`: Una lista de 3 objetos JSON, donde cada objeto representa un versículo de la misma version biblica para meditar y tiene las siguientes claves: - cita: La referencia del versículo (ej. 'Filipenses 4:6'), - texto: El texto del versículo (ej. 'Por nada estéis afanosos...').",
            "- `oracion`: Una oración relacionada con el tema del devocional (150 palabras). DEBE finalizar con la frase 'en el nombre de Jesús, amén' traducida correctamente al idioma de generación.",
            "- `tags`: Una lista de 2 palabras clave (ej. ['Fe', 'Esperanza'] palabra individual).",
            f"Asegúrate de que la cita del versículo principal en la clave `versiculo` sea idéntica a '{verse}' en su formato completo (Libro Capítulo:Versículo)."
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
        # Sobrescribir el campo 'id' para que sea siempre '<VERSION>-<YYYYMMDD>'
        devocional_data['id'] = f"{version}-{current_date.strftime('%Y%m%d')}"
       # Asegurar que 'version' solo contenga el valor del parámetro 'version' (el prompt)
        devocional_data['version'] = version
        
        # --- RECONSTRUCCIÓN ROBUSTA DEL VERSÍCULO PRINCIPAL ---
        if 'versiculo' in devocional_data:
            # 1. Extraer el TEXTO BÍBLICO PURO: Buscamos el contenido que sigue al primer ':'
            parts = devocional_data['versiculo'].split(':', 1)
            
            # Tomamos la segunda parte (el texto) si existe, y limpiamos las comillas externas.
            texto_bruto_con_prefijo = (parts[1] if len(parts) > 1 else devocional_data['versiculo']).strip().strip('"')
            
            # 2. LIMPIEZA PROFUNDA: Usar Regex para eliminar prefijos basura conocidos
            # Utilizamos re.escape(version) para que el nombre de la versión sea dinámico y seguro dentro del Regex.
            # Patrón busca: (Número de versículo)? (JA-)? (Nombre de la versión dinámica)? (caracteres de puntuación/espacios)?
            clean_text = re.sub(fr'^(?:\d+[ -]?)?(?:JA-)?(?:{re.escape(version)})?[":\s]*', '', texto_bruto_con_prefijo, 1).strip().strip('"')

            # 3. Reconstruir la cadena 'versiculo' con el formato exacto requerido:
            # [Cita limpia] [VERSION limpia]: "[Texto extraído]"
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
