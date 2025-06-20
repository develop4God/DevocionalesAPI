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
GENERATION_QUANTITY = 365  #Cantidad de devocionales a generar
# --- Fecha de Inicio ---
START_DATE = date(2026,6,12) # Fecha de inicio para la generación de devocionales
GENERATION_TOPIC = None
GENERATION_MAIN_VERSE_HINT = None

LANGUAGES_TO_GENERATE = ["es"]
VERSIONS_ES_TO_GENERATE = ["RVR1960"]
VERSIONS_EN_TO_GENERATE = []

# MODIFICADO: La función ahora procesa los devocionales uno por uno.
def generate_devotionals_iteratively():
    """
    Genera devocionales de forma iterativa (uno por día), manejando errores
    individualmente y guardando todos los resultados exitosos en un único archivo final.
    """
    # NUEVO: Lista para acumular los devocionales generados con éxito.
    successful_devotionals = []
    # NUEVO: Contadores para el resumen final.
    success_count = 0
    error_count = 0

    print(f"INFO: Iniciando generación de {GENERATION_QUANTITY} devocionales, desde {START_DATE.isoformat()}.")
    print("-" * 50)

    # NUEVO: Bucle principal que itera por cada día que se quiere generar.
    for i in range(GENERATION_QUANTITY):
        current_date = START_DATE + timedelta(days=i)
        print(f"Procesando día {i+1}/{GENERATION_QUANTITY}: {current_date.isoformat()}...")

        # --- Construcción de la solicitud para un solo día ---
        other_versions_dict = {}
        if VERSIONS_EN_TO_GENERATE:
            other_versions_dict["en"] = VERSIONS_EN_TO_GENERATE
        if VERSIONS_ES_TO_GENERATE:
            filtered_es_versions = [v for v in VERSIONS_ES_TO_GENERATE if v != VERSIONS_ES_TO_GENERATE[0]]
            if filtered_es_versions:
                other_versions_dict["es"] = filtered_es_versions

        # MODIFICADO: El payload ahora es para una única fecha.
        request_payload = {
            "start_date": current_date.isoformat(),
            "end_date": current_date.isoformat(), # La fecha de inicio y fin son la misma.
            "master_lang": LANGUAGES_TO_GENERATE[0] if LANGUAGES_TO_GENERATE else "es",
            "master_version": VERSIONS_ES_TO_GENERATE[0] if VERSIONS_ES_TO_GENERATE else "RVR1960",
            "other_versions": other_versions_dict,
            "topic": GENERATION_TOPIC,
            "main_verse_hint": GENERATION_MAIN_VERSE_HINT
        }

        # NUEVO: Bloque try-except dentro del bucle para capturar errores por día.
        try:
            response = requests.post(API_URL, json=request_payload, timeout=300)
            response.raise_for_status()  # Lanza excepción para errores HTTP (4xx o 5xx)

            json_response = response.json()

            # Ajustamos la extracción de datos según el formato de Devocionales_20250613_170859_es_RVR1960-NVI.json
            # Formato esperado de la API: {"data": {"es": {"YYYY-MM-DD": [...]}}}
            # Verificamos si la respuesta contiene el devocional en el formato esperado.
            devotional_data = None
            lang_code = LANGUAGES_TO_GENERATE[0] if LANGUAGES_TO_GENERATE else "es"
            date_key = current_date.isoformat()

            if isinstance(json_response, dict) and \
               "data" in json_response and \
               isinstance(json_response["data"], dict) and \
               lang_code in json_response["data"] and \
               isinstance(json_response["data"][lang_code], dict) and \
               date_key in json_response["data"][lang_code] and \
               isinstance(json_response["data"][lang_code][date_key], list) and \
               len(json_response["data"][lang_code][date_key]) > 0:

                devotional_data = json_response["data"][lang_code][date_key][0]
                # Verificar si el devocional es un error antes de agregarlo
                if devotional_data.get("id") and not devotional_data.get("id", "").startswith("error_") and "ERROR EN LA GENERACIÓN" not in devotional_data.get("versiculo", ""):
                    successful_devotionals.append(devotional_data)
                    success_count += 1
                    print(f"  -> ÉXITO: Devocional para {current_date.isoformat()} generado y agregado.")
                else:
                    error_count += 1
                    error_message = devotional_data.get("reflexion", "Error desconocido en la generación.")
                    devotional_id = devotional_data.get("id", "N/A")
                    print(f"  -> ERROR: Devocional '{devotional_id}' para {current_date.isoformat()} falló. Mensaje: {error_message}")
            else:
                error_count += 1
                print(f"  -> ADVERTENCIA: La respuesta de la API para {current_date.isoformat()} no contiene los datos esperados en el formato correcto.")
        except requests.exceptions.Timeout as timeout_err:
            error_count += 1
            print(f"  -> ERROR DE TIEMPO DE ESPERA para {current_date.isoformat()}: La API tardó demasiado en responder. {timeout_err}")
        except requests.exceptions.RequestException as req_err:
            error_count += 1
            print(f"  -> ERROR en la solicitud para {current_date.isoformat()}: {req_err}")
        except json.JSONDecodeError as json_err:
            error_count += 1
            print(f"  -> ERROR de JSON para {current_date.isoformat()}. No se pudo decodificar la respuesta de la API: {json_err}")
        except Exception as e:
            error_count += 1
            print(f"  -> ERROR INESPERADO para {current_date.isoformat()}: Tipo: {type(e).__name__}, Mensaje: {e}. Respuesta recibida: {json_response if 'json_response' in locals() else 'N/A'}")

        # Pequeña pausa para no saturar la API
        time.sleep(1)

    print("-" * 50)
    print("INFO: Proceso de generación finalizado.")
    print(f"Resumen: {success_count} devocionales generados con éxito, {error_count} fallidos.")

    # MODIFICADO: Guardado del archivo al final del proceso, con el formato esperado por la app.
    if successful_devotionals:
        # Reconstruimos la estructura anidada: {"data": {"idioma": {"fecha": [devocional]}}}
        nested_output_data = {}
        lang_code_for_output = LANGUAGES_TO_GENERATE[0] if LANGUAGES_TO_GENERATE else "es"
        
        # Inicializar la estructura para el idioma principal si no existe
        if lang_code_for_output not in nested_output_data:
            nested_output_data[lang_code_for_output] = {}

        for devocional in successful_devotionals:
            devocional_date = devocional.get("date") # Obtener la fecha del propio devocional
            if devocional_date:
                # Asegurarse de que la lista para esa fecha exista
                if devocional_date not in nested_output_data[lang_code_for_output]:
                    nested_output_data[lang_code_for_output][devocional_date] = []
                nested_output_data[lang_code_for_output][devocional_date].append(devocional)
            else:
                print(f"ADVERTENCIA: Devocional sin clave 'date', no se pudo anidar correctamente: {devocional.get('id', 'N/A')}")

        final_output_data = {"data": nested_output_data}

        current_timestamp_for_filename = datetime.now()
        output_filename = f"Devocionales_{current_timestamp_for_filename.strftime('%Y%m%d_%H%M%S')}_{LANGUAGES_TO_GENERATE[0]}_{VERSIONS_ES_TO_GENERATE[0]}.json"
        output_path = os.path.join(OUTPUT_BASE_DIR, output_filename)

        os.makedirs(OUTPUT_BASE_DIR, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(final_output_data, f, ensure_ascii=False, indent=4)

        print(f"\nÉXITO: {success_count} devocionales guardados en '{output_path}' con el formato compatible.")
    else:
        print("\nADVERTENCIA: No se generó ningún devocional con éxito. No se ha creado ningún archivo de salida.")

    print("INFO: Script finalizado.")


if __name__ == "__main__":
    if not os.path.exists(OUTPUT_BASE_DIR):
        os.makedirs(OUTPUT_BASE_DIR)
        print(f"INFO: Creado directorio de salida: {OUTPUT_BASE_DIR}")
    
    # MODIFICADO: Se llama a la nueva función iterativa.
    generate_devotionals_iteratively()