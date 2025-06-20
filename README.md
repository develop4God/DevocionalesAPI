Generador de Devocionales Bíblicos
🌐 Selecciona tu Idioma / Select your Language
Español (ES)

English (EN)

Español (ES)
Este proyecto consta de dos componentes principales: un servidor FastAPI (API_Server.py) que utiliza el modelo Google Gemini para generar devocionales bíblicos y un cliente Python (API_Client.py) que interactúa con este servidor para automatizar la generación de un conjunto de devocionales.

🚀 Características
Generación de Devocionales: Utiliza el modelo gemini-2.0-flash-lite para crear devocionales personalizados.

Soporte Multi-versión y Multi-idioma: Genera devocionales en español (RVR1960) y, potencialmente, otras versiones e idiomas (inglés: KJV).

Gestión de Versículos Excluidos: Mantiene un registro de versículos ya utilizados para evitar repeticiones.

Reintentos Automáticos: El servidor implementa lógica de reintento para las llamadas a la API de Gemini.

Generación Iterativa: El cliente permite generar devocionales día a día, gestionando errores individuales y acumulando resultados exitosos.

Salida Estructurada: Los devocionales generados se guardan en un archivo JSON estructurado y anidado por idioma y fecha.

🏗️ Arquitectura del Sistema
El sistema se compone de un servidor API y un cliente, interactuando de la siguiente manera:

Diagrama Conceptual de la Arquitectura
graph TD
    A[API_Client.py] -->|Solicitud HTTP POST| B(API_Server.py)
    B -->|Llamada a Gemini API| C(Google Gemini LLM)
    C -->|Respuesta de Contenido| B
    B -->|Respuesta JSON| A
    A -->|Guarda JSON| D[output_devocionales/]
    B -->|Actualiza/Guarda JSON| E[excluded_verses.json]

Descripción del Flujo:

El API_Client.py inicia una solicitud HTTP POST al API_Server.py para generar devocionales.

El API_Server.py selecciona un versículo (evitando los excluidos) y formula un prompt para el modelo Gemini.

El API_Server.py envía la solicitud al modelo gemini-2.0-flash-lite (Google Gemini LLM).

El modelo Gemini procesa el prompt y devuelve el contenido del devocional.

El API_Server.py valida y procesa la respuesta, añadiendo el versículo utilizado a la lista de excluidos y guardando esta lista en excluded_verses.json.

El API_Server.py envía una respuesta JSON estructurada de vuelta al API_Client.py.

El API_Client.py acumula los devocionales generados con éxito y los guarda en un archivo JSON dentro del directorio output_devocionales/.

🛠️ Especificaciones Técnicas
1. API_Server.py (Servidor FastAPI)
Framework: FastAPI

Modelo LLM: Google Gemini (gemini-2.0-flash-lite)

Manejo de Errores:

HTTPException para errores de API.

Decorador @retry (de tenacity) para reintentar llamadas a Gemini en caso de fallos.

Gestión de errores de decodificación JSON.

Configuración:

Carga la clave API de Gemini desde una variable de entorno (GOOGLE_API_KEY).

GenerationConfig global para Gemini (temperatura, top_p, top_k, max_output_tokens).

SafetySettings configurados para BLOCK_NONE en todas las categorías de daño.

Modelos de Datos (Pydantic):

ParaMeditarItem: Para citas y textos de meditación.

DevotionalContent: Estructura para cada devocional generado.

GenerateRequest: Define la estructura de la solicitud de generación.

LanguageData: Anida los devocionales por idioma y fecha.

ApiResponse: Estructura de la respuesta general de la API.

Funcionalidades Clave:

load_excluded_verses() y save_excluded_verses(): Para persistir los versículos ya utilizados.

create_error_devocional(): Genera una respuesta de error estandarizada.

obtener_todos_los_versiculos_posibles(): Punto de Adaptación – Donde se deben cargar los versículos bíblicos disponibles. Actualmente, es un set fijo de versículos del Nuevo Testamento.

get_abbreviated_verse_citation(): Convierte el nombre completo del libro a su abreviatura (ej., "Juan" -> "Jn").

extract_verse_from_content(): Extrae y normaliza el versículo de la respuesta de Gemini.

seleccionar_versiculo_para_generacion(): Selecciona un versículo principal, priorizando una pista (main_verse_hint) si es válida y no está excluida, o uno aleatorio si no.

generate_devocional_content_gemini(): Envía el prompt a Gemini y procesa la respuesta.

2. API_Client.py (Cliente Python)
Comunicación: Utiliza la librería requests para hacer solicitudes HTTP al servidor.

Parámetros de Generación:

API_URL: URL del endpoint del servidor FastAPI.

OUTPUT_BASE_DIR: Directorio donde se guardarán los archivos JSON de salida.

GENERATION_QUANTITY: Número de devocionales a generar.

START_DATE: Fecha de inicio para la generación.

GENERATION_TOPIC, GENERATION_MAIN_VERSE_HINT: Parámetros opcionales para guiar la generación.

LANGUAGES_TO_GENERATE, VERSIONS_ES_TO_GENERATE, VERSIONS_EN_TO_GENERATE: Listas de idiomas y versiones a generar.

Funcionalidad Principal:

generate_devotionals_iteratively():

Itera día a día, enviando una solicitud individual por cada fecha.

Maneja try-except para capturar errores de red, timeout, JSON y otros errores inesperados por cada solicitud.

Acumula los devocionales generados con éxito en una lista.

Pausa de 1 segundo entre solicitudes para evitar saturar la API.

Guarda todos los devocionales exitosos en un único archivo JSON al finalizar, con una estructura anidada por idioma y fecha.

Formato de Salida: El archivo JSON final tiene la estructura:

{
    "data": {
        "es": {
            "YYYY-MM-DD": [
                { /* Devocional 1 */ },
                { /* Devocional 2 (si aplica para otra versión del mismo día) */ }
            ],
            "YYYY-MM-DD": [
                { /* Devocional para el siguiente día */ }
            ]
        },
        "en": {
            "YYYY-MM-DD": [
                { /* Devocional en inglés */ }
            ]
        }
    }
}

⚙️ Configuración y Ejecución
Requisitos Previos
Python 3.9+

pip (gestor de paquetes de Python)

1. Instalación de Dependencias
Navega a la raíz del proyecto y ejecuta:

pip install -r requirements.txt

(Si no tienes un requirements.txt, puedes crearlo con las siguientes librerías):

fastapi
uvicorn
python-dotenv
google-generativeai
pydantic
requests
tenacity

2. Configuración de la Clave API de Gemini
Crea un archivo .env en la raíz del proyecto (al mismo nivel que API_Server.py y API_Client.py) con tu clave API de Google Gemini:

GOOGLE_API_KEY="TU_CLAVE_API_DE_GEMINI_AQUI"

3. Ejecución del Servidor
El servidor se ejecuta usando uvicorn. Abre tu terminal, navega a la raíz del proyecto y ejecuta:

uvicorn API_Server:app --host 0.0.0.0 --port 50000 --reload

API_Server:app: Indica a uvicorn que encuentre la aplicación app dentro del archivo API_Server.py.

--host 0.0.0.0: Permite que el servidor sea accesible desde cualquier IP (útil para pruebas en red local).

--port 50000: Especifica el puerto donde se ejecutará la API.

--reload: Recarga el servidor automáticamente al detectar cambios en el código (útil para desarrollo).

El servidor estará disponible en http://127.0.0.1:50000 (o http://localhost:50000). Puedes acceder a la documentación interactiva de la API en http://127.0.0.1:50000/docs.

4. Ejecución del Cliente
Una vez que el servidor esté en funcionamiento, abre otra terminal (no cierres la del servidor), navega a la raíz del proyecto y ejecuta el cliente:

python API_Client.py

El cliente comenzará a realizar solicitudes al servidor, y verás el progreso en ambas terminales. Los devocionales generados se guardarán en el directorio output_devocionales/.

🔄 Flujo de Trabajo
Diagrama de Flujo del Cliente (API_Client.py)
graph TD
    A[Inicio Cliente] --> B{Configurar Parámetros (cantidad, fecha, etc.)}
    B --> C[Inicializar Contadores y Lista de Devocionales Exitosos]
    C --> D{Bucle: Para cada día a generar}
    D --> E[Calcular Fecha Actual]
    E --> F[Construir Payload de Solicitud (para 1 día)]
    F --> G{Intentar Llamada a API_Server}
    G -- Éxito --> H[Decodificar Respuesta JSON]
    H --> I{Validar Devocional y NO es Error}
    I -- Sí --> J[Añadir Devocional a Lista de Exitosos]
    I -- No --> K[Incrementar Contador de Errores]
    G -- Error (Timeout, Red, JSON, etc.) --> K
    K --> L[Imprimir Mensaje de Error/Advertencia]
    J --> M[Imprimir Mensaje de Éxito]
    L --> N{Pausa de 1 segundo}
    M --> N
    N --> D
    D -- Fin Bucle --> O{Hay Devocionales Exitosos?}
    O -- Sí --> P[Reconstruir Estructura Anidada por Fecha/Idioma]
    P --> Q[Generar Nombre de Archivo y Ruta]
    Q --> R[Guardar JSON en output_devocionales/]
    O -- No --> S[Imprimir Advertencia: No se generó nada]
    R --> T[Fin Cliente]
    S --> T

Diagrama de Flujo del Servidor (API_Server.py)
graph TD
    A[Inicio Servidor] --> B[Cargar Versículos Excluidos]
    B --> C[Endpoint /generate_devotionals (POST)]
    C --> D{Bucle: Desde start_date hasta end_date}
    D --> E[Seleccionar main_verse (con hint/aleatorio, excluyendo usados)]
    E --> F{Intentar Generar master_version con Gemini}
    F -- Éxito --> G[Añadir main_verse a Excluidos]
    G --> H[Añadir master_devocional a response_data]
    H --> I{Bucle: Para otras_versions}
    I --> J{Intentar Generar other_version con Gemini (mismo main_verse)}
    J -- Éxito --> K[Añadir other_version_devocional a response_data]
    J -- Error --> L[Añadir Devocional de Error para other_version]
    K --> I
    L --> I
    I -- Fin Bucle --> D
    F -- Error (ValueError, HTTPException, etc.) --> M[Crear Error Devocional para master_version]
    M --> N[Crear Error Devocional para otras_versions]
    N --> D
    D -- Fin Bucle --> O[Guardar Versículos Excluidos]
    O --> P[Retornar ApiResponse (status, message, data)]

📝 Notas y Consideraciones
API Key: Asegúrate de que tu GOOGLE_API_KEY sea confidencial y no se suba a repositorios públicos.

Versículos Posibles: La función obtener_todos_los_versiculos_posibles() en API_Server.py contiene una lista fija de versículos del Nuevo Testamento. Para un uso más robusto, esta función debería cargar los versículos desde una fuente externa (ej., una base de datos, un archivo más grande, etc.).

Modelo Gemini: Actualmente utiliza gemini-2.0-flash-lite. Si deseas usar otro modelo, puedes modificar la línea model = genai.GenerativeModel('gemini-2.0-flash-lite', ...) en API_Server.py.

Personalización del Prompt: Puedes ajustar el prompt en generate_devocional_content_gemini() para refinar la calidad y el estilo de los devocionales generados.

Manejo de Errores: Aunque se implementan reintentos y devocionales de error, siempre es buena práctica monitorear los logs para identificar patrones de fallos recurrentes.

Escalabilidad: Para grandes volúmenes de generación o despliegues en producción, considera añadir colas de mensajes (ej., RabbitMQ, Celery) para procesar las solicitudes de forma asíncrona y robusta.

English (EN)
This project consists of two main components: a FastAPI server (API_Server.py) that uses the Google Gemini model to generate biblical devotionals, and a Python client (API_Client.py) that interacts with this server to automate the generation of a set of devotionals.

🚀 Features
Devotional Generation: Uses the gemini-2.0-flash-lite model to create personalized devotionals.

Multi-version and Multi-language Support: Generates devotionals in Spanish (RVR1960) and, potentially, other versions and languages (English: KJV).

Excluded Verse Management: Keeps a record of already used verses to avoid repetition.

Automatic Retries: The server implements retry logic for Gemini API calls.

Iterative Generation: The client allows generating devotionals day by day, handling individual errors and accumulating successful results.

Structured Output: Generated devotionals are saved in a structured JSON file nested by language and date.

🏗️ System Architecture
The system consists of an API server and a client, interacting as follows:

Conceptual Architecture Diagram
graph TD
    A[API_Client.py] -->|HTTP POST Request| B(API_Server.py)
    B -->|Gemini API Call| C(Google Gemini LLM)
    C -->|Content Response| B
    B -->|JSON Response| A
    A -->|Saves JSON| D[output_devocionales/]
    B -->|Updates/Saves JSON| E[excluded_verses.json]

Flow Description:

API_Client.py initiates an HTTP POST request to API_Server.py to generate devotionals.

API_Server.py selects a verse (avoiding excluded ones) and formulates a prompt for the Gemini model.

API_Server.py sends the request to the gemini-2.0-flash-lite model (Google Gemini LLM).

The Gemini model processes the prompt and returns the devotional content.

API_Server.py validates and processes the response, adding the used verse to the excluded list and saving this list to excluded_verses.json.

API_Server.py sends a structured JSON response back to API_Client.py.

API_Client.py accumulates successfully generated devotionals and saves them to a JSON file within the output_devocionales/ directory.

🛠️ Technical Specifications
1. API_Server.py (FastAPI Server)
Framework: FastAPI

LLM Model: Google Gemini (gemini-2.0-flash-lite)

Error Handling:

HTTPException for API errors.

@retry decorator (from tenacity) to retry Gemini calls in case of failures.

JSON decoding error handling.

Configuration:

Loads Gemini API key from an environment variable (GOOGLE_API_KEY).

Global GenerationConfig for Gemini (temperature, top_p, top_k, max_output_tokens).

SafetySettings configured to BLOCK_NONE for all harm categories.

Data Models (Pydantic):

ParaMeditarItem: For meditation quotes and texts.

DevotionalContent: Structure for each generated devotional.

GenerateRequest: Defines the structure of the generation request.

LanguageData: Nests devotionals by language and date.

ApiResponse: Structure of the general API response.

Key Functions:

load_excluded_verses() and save_excluded_verses(): To persist already used verses.

create_error_devocional(): Generates a standardized error response.

obtener_todos_los_versiculos_posibles(): Adaptation Point – Where available biblical verses should be loaded. Currently, it is a fixed set of New Testament verses.

get_abbreviated_verse_citation(): Converts the full book name to its abbreviation (e.g., "Juan" -> "Jn").

extract_verse_from_content(): Extracts and normalizes the verse from Gemini's response.

seleccionar_versiculo_para_generacion(): Selects a main verse, prioritizing a hint (main_verse_hint) if valid and not excluded, or a random one otherwise.

generate_devocional_content_gemini(): Sends the prompt to Gemini and processes the response.

2. API_Client.py (Python Client)
Communication: Uses the requests library to make HTTP requests to the server.

Generation Parameters:

API_URL: URL of the FastAPI server endpoint.

OUTPUT_BASE_DIR: Directory where output JSON files will be saved.

GENERATION_QUANTITY: Number of devotionals to generate.

START_DATE: Start date for generation.

GENERATION_TOPIC, GENERATION_MAIN_VERSE_HINT: Optional parameters to guide generation.

LANGUAGES_TO_GENERATE, VERSIONS_ES_TO_GENERATE, VERSIONS_EN_TO_GENERATE: Lists of languages and versions to generate.

Main Functionality:

generate_devotionals_iteratively():

Iterates day by day, sending an individual request for each date.

Handles try-except to catch network, timeout, JSON, and other unexpected errors for each request.

Accumulates successfully generated devotionals in a list.

1-second pause between requests to avoid API saturation.

Saves all successful devotionals to a single JSON file upon completion, with a nested structure by language and date.

Output Format: The final JSON file has the structure:

{
    "data": {
        "es": {
            "YYYY-MM-DD": [
                { /* Devotional 1 */ },
                { /* Devotional 2 (if applicable for another version on the same day) */ }
            ],
            "YYYY-MM-DD": [
                { /* Devotional for the next day */ }
            ]
        },
        "en": {
            "YYYY-MM-DD": [
                { /* English Devotional */ }
            ]
        }
    }
}

⚙️ Setup and Execution
Prerequisites
Python 3.9+

pip (Python package installer)

1. Install Dependencies
Navigate to the project root and run:

pip install -r requirements.txt

(If you don't have a requirements.txt, you can create it with the following libraries):

fastapi
uvicorn
python-dotenv
google-generativeai
pydantic
requests
tenacity

2. Configure Gemini API Key
Create a .env file in the project root (at the same level as API_Server.py and API_Client.py) with your Google Gemini API key:

GOOGLE_API_KEY="YOUR_GEMINI_API_KEY_HERE"

3. Run the Server
The server runs using uvicorn. Open your terminal, navigate to the project root, and run:

uvicorn API_Server:app --host 0.0.0.0 --port 50000 --reload

API_Server:app: Tells uvicorn to find the app application within the API_Server.py file.

--host 0.0.0.0: Allows the server to be accessible from any IP (useful for local network testing).

--port 50000: Specifies the port where the API will run.

--reload: Automatically reloads the server when code changes are detected (useful for development).

The server will be available at http://127.0.0.1:50000 (or http://localhost:50000). You can access the interactive API documentation at http://127.0.0.1:50000/docs.

4. Run the Client
Once the server is running, open another terminal (do not close the server's terminal), navigate to the project root, and run the client:

python API_Client.py

The client will start making requests to the server, and you will see the progress in both terminals. Generated devotionals will be saved in the output_devocionales/ directory.

🔄 Workflow
Client Flow Diagram (API_Client.py)
graph TD
    A[Client Start] --> B{Configure Parameters (quantity, date, etc.)}
    B --> C[Initialize Counters and List of Successful Devotionals]
    C --> D{Loop: For each day to generate}
    D --> E[Calculate Current Date]
    E --> F[Build Request Payload (for 1 day)]
    F --> G{Attempt API_Server Call}
    G -- Success --> H[Decode JSON Response]
    H --> I{Validate Devotional and NOT Error}
    I -- Yes --> J[Add Devotional to Successful List]
    I -- No --> K[Increment Error Counter]
    G -- Error (Timeout, Network, JSON, etc.) --> K
    K --> L[Print Error/Warning Message]
    J --> M[Print Success Message]
    L --> N{1-second Pause}
    M --> N
    N --> D
    D -- End Loop --> O{Are there Successful Devotionals?}
    O -- Yes --> P[Reconstruct Nested Structure by Date/Language]
    P --> Q[Generate File Name and Path]
    Q --> R[Save JSON to output_devocionales/]
    O -- No --> S[Print Warning: Nothing generated]
    R --> T[Client End]
    S --> T

Server Flow Diagram (API_Server.py)
graph TD
    A[Server Start] --> B[Load Excluded Verses]
    B --> C[Endpoint /generate_devotionals (POST)]
    C --> D{Loop: From start_date to end_date}
    D --> E[Select main_verse (with hint/random, excluding used)]
    E --> F{Attempt to Generate master_version with Gemini}
    F -- Success --> G[Add main_verse to Excluded]
    G --> H[Add master_devocional to response_data]
    H --> I{Loop: For other_versions}
    I --> J{Attempt to Generate other_version with Gemini (same main_verse)}
    J -- Success --> K[Add other_version_devocional to response_data]
    J -- Error --> L[Add Error Devotional for other_version]
    K --> I
    L --> I
    I -- End Loop --> D
    F -- Error (ValueError, HTTPException, etc.) --> M[Create Error Devotional for master_version]
    M --> N[Create Error Devotional for other_versions]
    N --> D
    D -- End Loop --> O[Save Excluded Verses]
    O --> P[Return ApiResponse (status, message, data)]

📝 Notes and Considerations
API Key: Make sure your GOOGLE_API_KEY is confidential and not uploaded to public repositories.

Possible Verses: The obtener_todos_los_versiculos_posibles() function in API_Server.py contains a fixed list of New Testament verses. For more robust use, this function should load verses from an external source (e.g., a database, a larger file, etc.).

Gemini Model: Currently uses gemini-2.0-flash-lite. If you want to use another model, you can modify the line model = genai.GenerativeModel('gemini-2.0-flash-lite', ...) in API_Server.py.

Prompt Customization: You can adjust the prompt in generate_devocional_content_gemini() to refine the quality and style of the generated devotionals.

Error Handling: Although retries and error devotionals are implemented, it's always good practice to monitor logs to identify patterns of recurring failures.

Scalability: For large volumes of generation or production deployments, consider adding message queues (e.g., RabbitMQ, Celery) to process requests asynchronously and robustly.
