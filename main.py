from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import google.generativeai as genai
import os
import re
import json
from dotenv import load_dotenv
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

load_dotenv()

# Inicializar Firebase Admin (para guardar citas)
try:
    cred = credentials.Certificate("firebase-adminsdk.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firebase Admin configurado correctamente")
except Exception as e:
    print(f"⚠️ Firebase Admin no configurado: {e}")
    db = None

# Configurar Gemini
try:
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model_texto = genai.GenerativeModel('gemini-2.5-flash-lite')
    print("✅ Gemini configurado correctamente")
except Exception as e:
    print(f"❌ Error configurando Gemini: {e}")

app = FastAPI()

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str
    historial: List[Dict[str, str]] = []
    tipo_paciente: str = "externo"

def extraer_datos_cita(texto: str):
    """Extrae nombre, hora y especialidad del texto usando regex"""
    nombre = None
    hora = None
    especialidad = None
    
    # Patrones para nombre (asume que el usuario dice "me llamo X" o "soy X")
    nombre_patterns = [
        r"(?:me llamo|soy|mi nombre es|nombre:?)\s+([A-Za-záéíóúñ\s]+?)(?=(?:\.|,|y|para|a las|$))",
        r"agendar(?:me)?\s+(?:una\s+)?cita\s+(?:con\s+)?(?:el|la)?\s+([A-Za-záéíóúñ\s]+?)(?=\s+(?:para|a las|de|$))"
    ]
    
    for pattern in nombre_patterns:
        match = re.search(pattern, texto, re.IGNORECASE)
        if match:
            nombre = match.group(1).strip()
            break
    
    # Patrones para hora
    hora_patterns = [
        r"(\d{1,2})(?::(\d{2}))?\s*(?:a\.?m\.?|p\.?m\.?|AM|PM)",
        r"a las\s+(\d{1,2})(?::(\d{2}))?\s*(?:a\.?m\.?|p\.?m\.?|AM|PM)?",
        r"(\d{1,2})\s*(?:de la)?\s*(mañana|tarde|noche)"
    ]
    
    for pattern in hora_patterns:
        match = re.search(pattern, texto, re.IGNORECASE)
        if match:
            hora_raw = match.group(0)
            # Normalizar hora
            if "am" in hora_raw.lower() or "mañana" in hora_raw.lower():
                hora = hora_raw
            elif "pm" in hora_raw.lower() or "tarde" in hora_raw.lower():
                hora = hora_raw
            else:
                hora = f"{hora_raw} AM" if int(match.group(1)) < 12 else f"{hora_raw} PM"
            break
    
    # Patrones para especialidad
    especialidades = {
        "general": "General",
        "ortodoncia": "Ortodoncia",
        "endodoncia": "Endodoncia",
        "pediatría": "Odontopediatría",
        "odontopediatría": "Odontopediatría",
        "cirugía": "Cirugía Oral"
    }
    
    texto_lower = texto.lower()
    for key, value in especialidades.items():
        if key in texto_lower:
            especialidad = value
            break
    
    return nombre, hora, especialidad

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    try:
        print(f"📨 Recibido mensaje: {req.message}")
        print(f"👤 Tipo paciente: {req.tipo_paciente}")
        
        # Primero, verificar si el usuario quiere agendar una cita
        mensaje_lower = req.message.lower()
        es_agendar = any(palabra in mensaje_lower for palabra in ["agendar", "cita", "reservar", "turno", "apartar"])
        
        if es_agendar:
            # Extraer datos de la conversación completa
            historial_completo = req.historial + [{"role": "user", "content": req.message}]
            texto_completo = " ".join([m["content"] for m in historial_completo])
            
            nombre, hora, especialidad = extraer_datos_cita(texto_completo)
            
            # Si tenemos los datos necesarios, agendar
            if nombre and hora:
                try:
                    cita_data = {
                        "paciente": nombre,
                        "tipo": "Estudiante UNEFA" if req.tipo_paciente == "estudiante" else "Paciente Externo",
                        "hora": hora,
                        "doctora": "Por asignar",
                        "estado": "Confirmada",
                        "fechaCreacion": datetime.now().isoformat(),
                        "especialidad": especialidad or "General"
                    }
                    
                    if db:
                        doc_ref = db.collection("citas").add(cita_data)
                        respuesta = f"✅ ¡Cita agendada con éxito!\n\n**Paciente:** {nombre}\n**Horario:** {hora}\n**Especialidad:** {especialidad or 'General'}\n\nRecuerda asistir puntualmente. Si eres estudiante, no olvides tu carnet."
                    else:
                        respuesta = f"✅ [DEMO] Cita agendada para {nombre} a las {hora}.\n\n(Nota: Firebase no está configurado, los datos no se guardaron realmente)"
                    
                    print(f"📅 Cita agendada: {nombre} - {hora}")
                    return {"reply": respuesta}
                    
                except Exception as e:
                    print(f"❌ Error guardando cita: {e}")
                    # Si falla, usamos Gemini para responder
            
            # Si faltan datos, usar Gemini para pedirlos
            if not nombre or not hora:
                prompt = f"""El usuario quiere agendar una cita. 
DATOS QUE TENGO:
- Nombre: {nombre if nombre else 'NO PROPORCIONADO'}
- Hora: {hora if hora else 'NO PROPORCIONADA'}
- Especialidad: {especialidad if especialidad else 'NO PROPORCIONADA'}

Tipo de paciente: {req.tipo_paciente}

RESPONDE DE MANERA AMABLE pidiendo ÚNICAMENTE los datos que faltan.
Si falta el nombre, pide nombre completo.
Si falta la hora, muestra los horarios disponibles: 8:00 AM, 9:00 AM, 10:00 AM, 11:00 AM, 1:00 PM, 2:00 PM, 3:00 PM, 4:00 PM.
Si falta la especialidad, sugiere: General, Ortodoncia, Endodoncia u Odontopediatría.

Respuesta corta y amable:"""

                response = model_texto.generate_content(prompt)
                return {"reply": response.text}
        
        # Si no es agendar cita, usar Gemini normalmente
        prompt = f"""Eres un asistente virtual AMABLE y PROFESIONAL para la Clínica Odontológica Sigo-UNEFA.

CONTEXTO:
- Tipo de paciente: {req.tipo_paciente} ("estudiante" = tarifa preferencial)
- Horarios disponibles: 8:00 AM, 9:00 AM, 10:00 AM, 11:00 AM, 1:00 PM, 2:00 PM, 3:00 PM, 4:00 PM
- Especialidades: General, Ortodoncia, Endodoncia, Odontopediatría

HISTORIAL RECIENTE:
{json.dumps(req.historial[-3:] if req.historial else [], indent=2)}

PREGUNTA DEL USUARIO: {req.message}

INSTRUCCIÓN: Responde de forma cálida y útil en español. Si el usuario quiere agendar cita, pídele nombre completo, horario y especialidad.

RESPUESTA:"""

        response = model_texto.generate_content(prompt)
        respuesta = response.text
        
        print(f"🤖 Respuesta generada: {respuesta[:100]}...")
        return {"reply": respuesta}
    
    except Exception as e:
        print(f"❌ Error: {e}")
        return {"reply": f"Lo siento, tuve un problema técnico. Por favor intenta de nuevo."}

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)