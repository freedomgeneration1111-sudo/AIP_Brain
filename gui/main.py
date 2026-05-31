from nicegui import ui
import requests
import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CONFIG_DIR = Path("config")
CONFIG_DIR.mkdir(exist_ok=True)
ENABLED_MODELS_FILE = CONFIG_DIR / "enabled_models.json"

def load_enabled_models():
    if ENABLED_MODELS_FILE.exists():
        return json.loads(ENABLED_MODELS_FILE.read_text())
    return ["llama3.2:3b", "openrouter/anthropic/claude-3.5-sonnet", "openrouter/openai/gpt-4o"]

def fetch_openrouter_models():
    try:
        r = requests.get("https://openrouter.ai/api/v1/models", timeout=15)
        if r.status_code == 200:
            data = r.json().get("data", [])
            models = []
            for m in data:
                arch = m.get("architecture", {})
                if arch.get("output_modalities") == ["text"]:
                    pricing = m.get("pricing", {})
                    models.append({
                        "id": m["id"],
                        "name": m.get("name", m["id"]),
                        "provider": m.get("owned_by", "Unknown"),
                        "input": round(float(pricing.get("prompt", 0)) * 1_000_000, 2),
                        "output": round(float(pricing.get("completion", 0)) * 1_000_000, 2),
                        "context": m.get("context_length", 0),
                    })
            models.sort(key=lambda x: x["input"])
            return models
    except Exception as e:
        ui.notify(f"Failed to load models from OpenRouter: {e}", color="negative")
    return []

def save_enabled_models(models):
    ENABLED_MODELS_FILE.write_text(json.dumps(models, indent=2))

@ui.page('/')
def main_page():
    ui.page_title("AIP_Brain")
    enabled = load_enabled_models()

    def get_ollama_status():
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=2)
            if r.status_code == 200:
                return "🟢 ollama running"
            return "🔴 ollama not connected"
        except:
            return "🔴 ollama not connected"

    def chat_with_ollama(prompt, model):
        try:
            url = "http://localhost:11434/api/generate"
            payload = {"model": model, "prompt": prompt, "stream": False}
            r = requests.post(url, json=payload, timeout=300)
            if r.status_code == 200:
                return r.json().get("response", "No response.")
            return f"Ollama error: {r.text}"
        except Exception as e:
            return f"Ollama error: {str(e)}"

    def chat_with_openrouter(prompt, model):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return "Error: OPENROUTER_API_KEY not set."
        try:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
            r = requests.post(url, headers=headers, json=payload, timeout=120)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            return f"OpenRouter error: {r.text}"
        except Exception as e:
            return f"OpenRouter error: {str(e)}"

    def send_prompt():
        prompt = input_field.value.strip()
        if not prompt: return
        add_message("user", prompt)
        input_field.value = ""
        thinking = ui.label("Thinking...").classes("text-grey")
        try:
            model = model_select.value
            reply = chat_with_openrouter(prompt, model) if "/" in model else chat_with_ollama(prompt, model)
            thinking.delete()
            add_message("assistant", reply, model=model)
        except Exception as e:
            thinking.delete()
            ui.notify(f"Error: {e}", color="negative")

    def add_message(role, text, model=None):
        with chat_container:
            with ui.row().classes("w-full"):
                display = model.replace("openrouter/", "") if model and role == "assistant" else role.capitalize()
                ui.markdown(f"**{display}**").classes("text-sm text-grey-7")
            with ui.row().classes("w-full"):
                color = "#dcf8c6" if role == "assistant" else "#f0f0f0"
                ui.markdown(text).classes("p-2 rounded-lg").style(f"background-color: {color}; max-width: 80%;")

    with ui.header(elevated=True).classes("bg-primary text-white items-center q-pa-sm"):
        ui.label("AIP_Brain").classes("text-h6 q-ml-md")
        ui.button("Normal Chat", on_click=lambda: set_mode("normal")).props("flat text-color=white")
        ui.button("Knowledge Augmented", on_click=lambda: set_mode("augmented"), color="yellow").props("flat outline")
        global mode_label
        mode_label = ui.label("Normal Chat").classes("q-ml-sm text-weight-medium text-white")
        ui.space()
        ui.label("Model:").classes("q-mr-xs text-white")
        global model_select
        model_select = ui.select(enabled, value=enabled[0] if enabled else None).classes("min-w-[280px] text-black")
        ui.checkbox("Auto-save", value=True).classes("q-ml-sm text-white")
        ui.space()
        ui.button("Models & Roles", on_click=lambda: ui.navigate.to('/models'), color="secondary").props("flat")
        ui.space()
        with ui.row().classes("items-center gap-1"):
            ui.button("Vector", icon="storage", on_click=lambda: ui.notify("Vector")).props("flat text-color=white")
            ui.button("Graph", icon="account_tree", on_click=lambda: ui.notify("Graph")).props("flat text-color=white")
            ui.button("Wiki", icon="menu_book", on_click=lambda: ui.notify("Wiki")).props("flat text-color=white")
            ui.button("Sources", icon="source", on_click=lambda: ui.notify("Sources")).props("flat text-color=white")

    with ui.right_drawer(fixed=True).classes("q-pa-md bg-grey-1"):
        ui.label("Role Assignments").classes("text-h6 q-mb-md")
        ui.label("Beast").classes("text-weight-medium")
        ui.select(enabled, value=enabled[0] if enabled else None).classes("q-mb-sm")
        ui.label("Vigil").classes("text-weight-medium")
        ui.select(enabled, value=enabled[0] if enabled else None).classes("q-mb-sm")
        ui.label("Embedding").classes("text-weight-medium")
        ui.select(enabled, value=enabled[0] if enabled else None).classes("q-mb-md")
        ui.button("Save Roles", color="primary")

    global chat_container, input_field
    chat_container = ui.column().classes("w-full max-w-3xl mx-auto q-pa-md").style("min-height: 400px;")

    with ui.row().classes("w-full max-w-3xl mx-auto items-center q-pa-sm gap-2"):
        input_field = ui.input(placeholder="Ask anything...").props("outlined dense").classes("flex-grow")
        input_field.on("keydown.enter", send_prompt)
        ui.button("Send", on_click=send_prompt, color="primary").props("icon=send")

    with ui.footer().classes("bg-grey-2 q-pa-xs items-center"):
        ui.label("AIP_Brain • Hybrid").classes("text-caption text-black")
        ui.space()
        ui.label().classes("text-caption text-black")
        ui.space()
        ui.label(get_ollama_status()).classes("text-caption text-black")

@ui.page('/models')
def model_catalog_page():
    ui.page_title("Model Catalog")
    ui.label("Model Catalog").classes("text-h4 q-my-md")
    ui.label("Select up to 12 models to enable. These will appear in role assignments and chat.").classes("text-subtitle2 q-mb-md")

    models = fetch_openrouter_models()
    if not models:
        ui.label("Could not load models from OpenRouter").classes("text-negative")
        return

    enabled = set(load_enabled_models())
    checkboxes = {}

    with ui.row().classes("w-full"):
        with ui.column().classes("w-full"):
            columns = [
                {"name": "name", "label": "Model", "field": "name"},
                {"name": "provider", "label": "Provider", "field": "provider"},
                {"name": "input", "label": "Input ($/M)", "field": "input"},
                {"name": "output", "label": "Output ($/M)", "field": "output"},
                {"name": "context", "label": "Context", "field": "context"},
            ]
            rows = [
                {
                    "name": m["name"],
                    "provider": m["provider"],
                    "input": f"${m['input']:.2f}",
                    "output": f"${m['output']:.2f}",
                    "context": f"{m['context']:,}",
                } for m in models
            ]
            ui.table(columns=columns, rows=rows, row_key="name").classes("w-full")

        with ui.column().classes("q-ml-md").style("min-width: 420px; max-height: 75vh; overflow-y: auto"):
            ui.label(f"Enable Models ({len(enabled)}/12)").classes("text-weight-medium q-mb-sm")
            for m in models:
                cb = ui.checkbox(m["name"], value=(m["id"] in enabled))
                checkboxes[m["id"]] = cb

    def save():
        selected = [mid for mid, cb in checkboxes.items() if cb.value]
        if len(selected) > 12:
            ui.notify("Maximum 12 models allowed", color="negative")
            return
        save_enabled_models(selected)
        ui.notify(f"Saved {len(selected)} models", color="positive")

    ui.button("Save Selection", on_click=save, color="primary").classes("q-mt-md")
    ui.button("Back to Chat", on_click=lambda: ui.navigate.to('/'), color="grey").classes("q-mt-sm")

ui.run(title="AIP_Brain", port=8080, reload=True)
