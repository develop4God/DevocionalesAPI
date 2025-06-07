import requests
import json
import os
from typing import Dict, Any, List
from datetime import date, timedelta, datetime
import time

print("INFO: Script cliente iniciado. Intentando conectar a la API...")

# --- Configuración del Script ---
API_URL = "http://127.0.0.1:50000/generate_devotionals"
OUTPUT_BASE_DIR = "output_devocionales"

# --- Parámetros de Generación ---
GENERATION_QUANTITY = 2
START_DATE = date(2025, 6, 6)
GENERATION_TOPIC = None
GENERATION_MAIN_VERSE_HINT = None

LANGUAGES_TO_GENERATE = ["es"]
VERSIONS_ES_TO_GENERATE = ["RVR1960"]
VERSIONS_EN_TO_GENERATE = []

def generate_devotionals_massively():
    """Genera devocionales en masa llamando a la API y los guarda en un único archivo."""
    end_date = START_DATE + timedelta(days=GENERATION_QUANTITY - 1)

    other_versions_dict = {}
    if VERSIONS_EN_TO_GENERATE:
        other_versions_dict["en"] = VERSIONS_EN_TO_GENERATE
    if VERSIONS_ES_TO_GENERATE:
        filtered_es_versions = [v for v in VERSIONS_ES_TO_GENERATE if v != VERSIONS_ES_TO_GENERATE[0]]
        if filtered_es_versions:
            other_versions_dict["es"] = filtered_es_versions

    payload = {
        "start_date": START_DATE.strftime('%Y-%m-%d'),
        "end_date": end_date.strftime('%Y-%m-%d'),
        "master_lang": LANGUAGES_TO_GENERATE[0],
        "master_version": VERSIONS_ES_TO_GENERATE[0] if LANGUAGES_TO_GENERATE[0] == "es" else VERSIONS_EN_TO_GENERATE[0],
        "other_versions": other_versions_dict,
        "topic": GENERATION_TOPIC,
        "main_verse_hint": GENERATION_MAIN_VERSE_HINT,
    }

    headers = {
        "Content-Type": "application/json"
    }

    print(f"INFO: Enviando solicitud a la API con payload: {json.dumps(payload, indent=2)}")

    try:
        response = requests.post(API_URL, json=payload, headers=headers, timeout=300)
        response.raise_for_status()

        api_response = response.json()
        print(f"INFO: Respuesta de la API recibida. Estado: {api_response.get('status')}, Mensaje: {api_response.get('message')}")

        if api_response.get("status") == "success":
            # Obtener la fecha y hora actual para el nombre del archivo
            current_datetime = datetime.now()
            date_time_str = current_datetime.strftime('%Y%m%d_%H%M%S')

            # Construir las cadenas de idioma y versión
            lang_part = "_".join(LANGUAGES_TO_GENERATE)
            versions_part_es = "_".join(VERSIONS_ES_TO_GENERATE)
            versions_part_en = "_".join(VERSIONS_EN_TO_GENERATE)
            
            # Combinar las versiones presentes
            all_versions = []
            if versions_part_es:
                all_versions.append(versions_part_es)
            if versions_part_en:
                all_versions.append(versions_part_en)
            versions_combined_part = "_".join(all_versions)

            # Crear el nombre del archivo único
            OUTPUT_SINGLE_FILE_NAME = f"Devocionales_{date_time_str}_{lang_part}_{versions_combined_part}.json"
            output_file_path = os.path.join(OUTPUT_BASE_DIR, OUTPUT_SINGLE_FILE_NAME)

            # Guardar la respuesta completa de la API, que incluye status, message y data
            with open(output_file_path, "w", encoding="utf-8") as f:
                json.dump(api_response, f, ensure_ascii=False, indent=4)
            print(f"INFO: Todos los devocionales generados guardados en {output_file_path}")

        else:
            print(f"ERROR: La API reportó un fallo: {api_response.get('message')}")
            if 'data' in api_response:
                print(f"Datos de error de la API: {json.dumps(api_response['data'], indent=2)}")

    except requests.exceptions.HTTPError as http_err:
        print(f"\nERROR HTTP CAPTURADO: {http_err}")
        print(f"Código de estado: {http_err.response.status_code}")
        try:
            error_details = http_err.response.json()
            print(f"Detalles del error de la API: {json.dumps(error_details, indent=2)}")
        except json.JSONDecodeError:
            print(f"Respuesta de error no JSON: {http_err.response.text}")
    except requests.exceptions.ConnectionError as conn_err:
        print(f"\nERROR DE CONEXIÓN CAPTURADO: {conn_err}")
        print("Asegúrate de que tu API de FastAPI esté corriendo en la URL configurada y sea accesible.")
    except requests.exceptions.Timeout as timeout_err:
        print(f"\nERROR DE TIEMPO DE ESPERA CAPTURADO: {timeout_err}")
        print("La API tardó demasiado en responder. Considera aumentar el 'timeout' o revisar el rendimiento de la API.")
    except requests.exceptions.RequestException as req_err:
        print(f"\nERROR INESPERADO EN LA SOLICITUD CAPTURADO: {req_err}")
        print("Esto podría incluir problemas con la URL, el esquema o la configuración de la solicitud.")
    except json.JSONDecodeError as json_err:
        print(f"\nERROR DE JSON CAPTURADO: No se pudo decodificar la respuesta JSON de la API: {json_err}")
        print(f"Texto de la respuesta: {response.text[:500]}..." if 'response' in locals() else "No hay respuesta para mostrar.")
    except Exception as e:
        print(f"\nERROR GENERAL INESPERADO CAPTURADO: {e}")
    finally:
        if GENERATION_QUANTITY > 1:
            print("INFO: Esperando 5 segundos antes de finalizar la ejecución.")
            time.sleep(5)

if __name__ == "__main__":
    if not os.path.exists(OUTPUT_BASE_DIR):
        os.makedirs(OUTPUT_BASE_DIR)
        print(f"INFO: Creado el directorio base para los devocionales: {OUTPUT_BASE_DIR}")
    
    generate_devotionals_massively()
