import requests
import json
import os
from typing import Dict, Any, List
from datetime import date, timedelta, datetime
import time

print("INFO: Script cliente iniciado. Intentando conectar a la API...")

# --- Configuracion del Script ---
API_URL = "http://127.0.0.1:50000/generate_devotionals"
OUTPUT_BASE_DIR = os.path.join(os.getcwd(), "output_devocionales")

# --- Parámetros de Generación ---
GENERATION_QUANTITY = 365  #Cantidad de devocionales a generar
# --- Fecha de Inicio ---
START_DATE = date(2025,8,1) # Fecha de inicio para la generación de devocionales
GENERATION_TOPIC = None
GENERATION_MAIN_VERSE_HINT = None


# Configuración simplificada para UN SOLO idioma y versión
# Puedes cambiar MASTER_LANG y MASTER_VERSION a cualquiera de estos:
# Español: "es", versión: "RVR1960"
# Inglés: "en", versión: "KJV"
# Portugués: "pt", versión: "ARC"
# Francés: "fr", versión: "LS1910"
# Chino simplificado: "zh", versión: "CUVS" (ejemplo)
# Japonés: "ja", versión: "JCB" (ejemplo)
MASTER_LANG = "ja"      # Cambia a "zh" para chino o "ja" para japonés
MASTER_VERSION = "新改訳2003"  # Cambia a "CUVS" para chino o "JCB" para japonés


#Referencia a otros parametros master
#VERSIONS_ES_TO_GENERATE = ["RVR1960"]#,"NVI"

#VERSIONS_EN_TO_GENERATE = ["KJV"]#,"NVI"

#VERSIONS_FR_TO_GENERATE = ["LS1910"]#,"TOB"

#VERSIONS_PT_TO_GENERATE = ["ARC"]#,"NVI"

def generate_devotionals_iteratively():
    """
    Genera devocionales de forma iterativa (uno por día), manejando errores
    individualmente y guardando todos los resultados exitosos en un único archivo final.
    """
    # Lista para acumular los devocionales generados con éxito.
    successful_devotionals = []
    # Contadores para el resumen final.
    success_count = 0
    error_count = 0

    print(f"INFO: Iniciando generación de {GENERATION_QUANTITY} devocionales en {MASTER_LANG}-{MASTER_VERSION}, desde {START_DATE.isoformat()}.")
    print("-" * 50)

    # Bucle principal que itera por cada día que se quiere generar.
    for i in range(GENERATION_QUANTITY):
        current_date = START_DATE + timedelta(days=i)
        print(f"Procesando día {i+1}/{GENERATION_QUANTITY}: {current_date.isoformat()}...")

        # Payload simplificado - SOLO idioma y versión maestros
        request_payload = {
            "start_date": current_date.isoformat(),
            "end_date": current_date.isoformat(), # La fecha de inicio y fin son la misma.
            "master_lang": MASTER_LANG,
            "master_version": MASTER_VERSION,
            "topic": GENERATION_TOPIC,
            "main_verse_hint": GENERATION_MAIN_VERSE_HINT
        }

        print(f"DEBUG: Enviando payload: {json.dumps(request_payload, indent=2)}")

        # Bloque try-except dentro del bucle para capturar errores por día.
        try:
            response = requests.post(API_URL, json=request_payload, timeout=300)
            response.raise_for_status()  # Lanza excepción para errores HTTP (4xx o 5xx)

            json_response = response.json()
            print(f"DEBUG: Respuesta recibida: {json.dumps(json_response, indent=2, ensure_ascii=False)}")

            # Verificamos si la respuesta contiene el devocional en el formato esperado.
            devotional_data = None
            date_key = current_date.isoformat()

            if isinstance(json_response, dict) and \
               "data" in json_response and \
               isinstance(json_response["data"], dict) and \
               MASTER_LANG in json_response["data"] and \
               isinstance(json_response["data"][MASTER_LANG], dict) and \
               date_key in json_response["data"][MASTER_LANG] and \
               isinstance(json_response["data"][MASTER_LANG][date_key], list) and \
               len(json_response["data"][MASTER_LANG][date_key]) > 0:

                devotional_data = json_response["data"][MASTER_LANG][date_key][0]
                # Verificar si el devocional es un error antes de agregarlo
                if devotional_data.get("id") and not devotional_data.get("id", "").startswith("error_") and "ERROR EN LA GENERACIÓN" not in devotional_data.get("versiculo", ""):
                    successful_devotionals.append(devotional_data)
                    success_count += 1
                    print(f"  -> EXITO: Devocional para {current_date.isoformat()} generado y agregado.")
                    print(f"    ID: {devotional_data.get('id', 'N/A')}")
                    print(f"    Versículo: {devotional_data.get('versiculo', 'N/A')}")
                else:
                    error_count += 1
                    error_message = devotional_data.get("reflexion", "Error desconocido en la generación.")
                    devotional_id = devotional_data.get("id", "N/A")
                    print(f"  -> ERROR: Devocional '{devotional_id}' para {current_date.isoformat()} falló. Mensaje: {error_message}")
            else:
                error_count += 1
                print(f"  -> ADVERTENCIA: La respuesta de la API para {current_date.isoformat()} no contiene los datos esperados en el formato correcto.")
                print(f"    Estructura esperada: data.{MASTER_LANG}.{date_key}[]")
                if isinstance(json_response, dict) and "data" in json_response:
                    print(f"    Idiomas disponibles en respuesta: {list(json_response['data'].keys()) if isinstance(json_response['data'], dict) else 'N/A'}")
                
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
            print(f"  -> ERROR INESPERADO para {current_date.isoformat()}: Tipo: {type(e).__name__}, Mensaje: {e}")
            if 'json_response' in locals():
                print(f"    Respuesta recibida: {json_response}")

        # Pequeña pausa para no saturar la API
        time.sleep(1)

    print("-" * 50)
    print("INFO: Proceso de generación finalizado.")
    print(f"Resumen: {success_count} devocionales generados con éxito, {error_count} fallidos.")

    # Guardado del archivo al final del proceso, con el formato esperado por la app.
    if successful_devotionals:
        # Reconstruimos la estructura anidada: {"data": {"idioma": {"fecha": [devocional]}}}
        nested_output_data = {}
        
        # Inicializar la estructura para el idioma principal
        if MASTER_LANG not in nested_output_data:
            nested_output_data[MASTER_LANG] = {}

        for devocional in successful_devotionals:
            devocional_date = devocional.get("date") # Obtener la fecha del propio devocional
            if devocional_date:
                # Asegurarse de que la lista para esa fecha exista
                if devocional_date not in nested_output_data[MASTER_LANG]:
                    nested_output_data[MASTER_LANG][devocional_date] = []
                nested_output_data[MASTER_LANG][devocional_date].append(devocional)
            else:
                print(f"ADVERTENCIA: Devocional sin clave 'date', no se pudo anidar correctamente: {devocional.get('id', 'N/A')}")

        final_output_data = {"data": nested_output_data}

        current_timestamp_for_filename = datetime.now()
        output_filename = f"Devocional_year_{current_timestamp_for_filename.strftime('%Y%m%d_%H%M%S')}_{MASTER_LANG}_{MASTER_VERSION}.json"
        output_path = os.path.join(OUTPUT_BASE_DIR, output_filename)
        
        # Asegurar que el directorio existe
        os.makedirs(OUTPUT_BASE_DIR, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(final_output_data, f, ensure_ascii=False, indent=4)

        print(f"\nÉXITO: {success_count} devocionales guardados en '{output_path}' con el formato compatible.")
        print(f"Directorio de salida: {OUTPUT_BASE_DIR}")
    else:
        print("\nADVERTENCIA: No se generó ningún devocional con éxito. No se ha creado ningún archivo de salida.")

    print("INFO: Script finalizado.")


if __name__ == "__main__":
    # Crear directorio de salida si no existe
    if not os.path.exists(OUTPUT_BASE_DIR):
        os.makedirs(OUTPUT_BASE_DIR)
        print(f"INFO: Creado directorio de salida: {OUTPUT_BASE_DIR}")
    
    print(f"INFO: Configuración activa - Idioma: {MASTER_LANG}, Versión: {MASTER_VERSION}")
    print(f"INFO: Directorio de salida: {OUTPUT_BASE_DIR}")
    
    # Llamar a la función iterativa.
    generate_devotionals_iteratively()
