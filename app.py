import streamlit as st
import sqlite3
import chromadb
import ollama
import json
import pandas as pd
import os
import re
import logging

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cortex")

# --- CONFIGURATION ---
st.set_page_config(page_title="Cortex", layout="wide", page_icon="🧠")

st.markdown("""
<style>
    pre, code, pre code {
        white-space: pre-wrap !important;
        word-break: break-word !important;
        overflow-wrap: break-word !important;
    }
    .stMarkdown pre, .stCode pre, div[data-testid="stMarkdownContainer"] pre, div[data-testid="stMarkdownContainer"] code {
        white-space: pre-wrap !important;
        word-wrap: break-word !important;
        overflow-x: hidden !important;
    }
</style>
""", unsafe_allow_html=True)

OLLAMA_HOST = os.environ.get("CORTEX_OLLAMA_HOST", "http://127.0.0.1:11434")
DB_DIR = os.environ.get("CORTEX_DB_DIR", "./memory_bank")
SQLITE_PATH = os.path.join(DB_DIR, "master_vault.db")
CHROMA_COLLECTION_NAME = "universal_vectors"
EMBED_MODEL = "nomic-embed-text"

DEFAULT_LIBRARIAN_MODEL = "qwen2.5:14b"
DEFAULT_CREATIVE_MODEL = "VladimirGav/gemma4-26b-16GB-VRAM:latest"
FALLBACK_MODELS = [DEFAULT_LIBRARIAN_MODEL, DEFAULT_CREATIVE_MODEL]

# --- SHARED CLIENTS ---
@st.cache_resource
def get_ollama_client():
    return ollama.Client(host=OLLAMA_HOST)

@st.cache_resource
def get_chroma_client():
    return chromadb.PersistentClient(path=DB_DIR)

def get_chroma_collection():
    return get_chroma_client().get_or_create_collection(name=CHROMA_COLLECTION_NAME)

def get_sqlite_connection():
    return sqlite3.connect(SQLITE_PATH)

def get_distinct_projects():
    """Returns the list of distinct project names currently in the vault."""
    try:
        conn = get_sqlite_connection()
        df = pd.read_sql_query("SELECT DISTINCT project FROM lore_records", conn)
        conn.close()
        return df['project'].tolist()
    except Exception:
        logger.exception("Failed to fetch distinct projects")
        return []

def get_record_count():
    try:
        conn = get_sqlite_connection()
        count = pd.read_sql_query("SELECT COUNT(*) as n FROM lore_records", conn).iloc[0]['n']
        conn.close()
        return int(count)
    except Exception:
        return None

@st.cache_data(ttl=30)
def check_ollama_status(_host: str) -> bool:
    try:
        ollama.Client(host=_host).list()
        return True
    except Exception:
        return False

# --- INITIALIZATION & API FETCHING ---
def get_available_models():
    """Fetches the list of installed models directly from Ollama."""
    try:
        client = get_ollama_client()
        models = client.list()
        return [m['model'] for m in models['models']]
    except Exception as e:
        logger.warning("Could not reach Ollama to list models, using fallback defaults: %s", e)
        return list(FALLBACK_MODELS)

if 'ollama_models' not in st.session_state:
    st.session_state.ollama_models = get_available_models()

if 'librarian_model' not in st.session_state:
    st.session_state.librarian_model = DEFAULT_LIBRARIAN_MODEL if DEFAULT_LIBRARIAN_MODEL in st.session_state.ollama_models else st.session_state.ollama_models[0]

if 'creative_model' not in st.session_state:
    st.session_state.creative_model = DEFAULT_CREATIVE_MODEL if DEFAULT_CREATIVE_MODEL in st.session_state.ollama_models else st.session_state.ollama_models[0]

# --- DATABASE SETUP ---
def init_databases():
    os.makedirs(DB_DIR, exist_ok=True)

    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lore_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT,
            category TEXT,
            subcategory TEXT,
            title TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

    get_chroma_collection()

# --- AI ORCHESTRATOR LOGIC ---
def get_project_context(query: str, project_name: str, n_results: int = 5):
    """Retrieves relevant vault records for grounding.

    Returns (context_string, error_message). error_message is None on success
    (including the "no matches found" case) so callers can distinguish a
    genuinely empty vault from a broken Ollama/Chroma connection.
    """
    try:
        client = get_ollama_client()
        embed_response = client.embeddings(model=EMBED_MODEL, prompt=query)

        collection = get_chroma_collection()

        if project_name == "All Projects" or not project_name:
            results = collection.query(
                query_embeddings=[embed_response['embedding']],
                n_results=n_results
            )
        else:
            results = collection.query(
                query_embeddings=[embed_response['embedding']],
                n_results=n_results,
                where={"project": project_name}
            )

        if not results['documents'] or not results['documents'][0]:
            return "", None

        context_string = "\n\n--- VAULT RECORDS (SOURCE OF TRUTH) ---\n"
        for i in range(len(results['documents'][0])):
            doc = results['documents'][0][i]
            meta = results['metadatas'][0][i]
            title = meta.get('title', 'Unknown Title')
            proj = meta.get('project', 'Unknown Project')
            context_string += f"[{proj} - {title}]: {doc}\n\n"

        return context_string, None
    except Exception as e:
        logger.exception("Failed to retrieve project context for query %r", query)
        return "", f"{type(e).__name__}: {e}"

def process_brain_dump(raw_text: str, project_name: str, context: str, output_container, librarian_model: str):
    proj_directive = f"Project Name: '{project_name}'" if project_name else "Project Name: NONE (You must invent a fitting project name based on the content)"

    system_prompt = f"""
    You are the Master Data Engineer archiving notes, journals, and creative ideas.

    {proj_directive}

    The user is providing a raw brain dump.
    Your job is to categorize it and format it, BUT YOU MUST PRESERVE THE USER'S ORIGINAL IDEAS EXACTLY.
    Do NOT add new lore, do NOT expand the text, and do NOT invent new details.
    You may apply clean Markdown formatting to make it readable, but the core text must remain true to the prompt.

    CRITICAL: You must output your ENTIRE response as a strict JSON object.

    You must use this exact JSON schema:
    {{
        "project": "String (Use the provided Project Name, or invent a punchy title if none provided)",
        "category": "String (Analyze the content's domain first. Is it Personal, Professional, Fictional, or Technical? Create a high-level category that fits exactly, e.g., 'Team Management', 'Personal Health', 'Worldbuilding', 'Software Architecture')",
        "subcategory": "String (Be highly specific to the entry, e.g., 'Performance Evaluation', 'Therapy Notes', 'Villain Backstory', 'Database Schema')",
        "title": "String (A short, punchy title summarizing the concept)",
        "content": "String (The user's original text, neatly formatted with Markdown)"
    }}

    {context}

    RULE: Do NOT default to fictional terms like 'Lore' or 'Character' unless the text is explicitly about a story or game. You must dynamically adapt your taxonomy to the real-world or fictional context of the user's input.
    """

    try:
        client = get_ollama_client()
        response_stream = client.chat(
            model=librarian_model,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': raw_text}
            ],
            format='json',
            stream=True
        )

        full_response = ""
        for chunk in response_stream:
            if 'message' in chunk and 'content' in chunk['message']:
                full_response += chunk['message']['content']
                output_container.markdown(f"**Under the hood (Raw JSON Output):**\n```json\n{full_response}▌\n```")

        output_container.markdown(f"**Under the hood (Raw JSON Output):**\n```json\n{full_response}\n```")

        try:
            return json.loads(full_response)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', full_response, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            else:
                logger.error("Librarian model returned unrecoverable JSON: %r", full_response)
                return {"error": "The model failed to output a recoverable JSON block."}

    except Exception as e:
        logger.exception("process_brain_dump failed")
        return {"error": str(e)}

# --- DATA MANAGEMENT LOGIC ---
def save_to_databases(structured_data: dict, fallback_project_name: str):
    """Writes a record to SQLite and indexes it in Chroma.

    Raises RuntimeError with a user-facing message if either step fails.
    If Chroma indexing fails after the SQLite insert, the SQLite row is
    rolled back so the two stores never silently diverge.
    """
    project = structured_data.get("project")
    if not project or project.strip() == "":
        project = fallback_project_name if fallback_project_name else "Untitled Project"

    category = structured_data.get("category", "Uncategorized")
    subcategory = structured_data.get("subcategory", "Misc")
    title = structured_data.get("title", "Untitled Concept")
    content = structured_data.get("content", "")

    try:
        client = get_ollama_client()
        embed_response = client.embeddings(model=EMBED_MODEL, prompt=content)
    except Exception as e:
        logger.exception("Embedding failed while saving record %r", title)
        raise RuntimeError(f"Could not generate embedding — record was NOT saved. ({e})") from e

    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO lore_records (project, category, subcategory, title, content)
        VALUES (?, ?, ?, ?, ?)
    ''', (project, category, subcategory, title, content))
    record_id = cursor.lastrowid
    conn.commit()
    conn.close()

    try:
        collection = get_chroma_collection()
        collection.add(
            ids=[f"rec_{record_id}"],
            embeddings=[embed_response['embedding']],
            documents=[content],
            metadatas=[{"project": project, "category": category, "subcategory": subcategory, "title": title}]
        )
    except Exception as e:
        logger.exception("Chroma indexing failed for record %s; rolling back SQLite row", record_id)
        conn = get_sqlite_connection()
        conn.execute("DELETE FROM lore_records WHERE id=?", (record_id,))
        conn.commit()
        conn.close()
        raise RuntimeError(f"Could not index record for search — the change was rolled back. ({e})") from e

    return title, category, project

def update_record(record_id: int, project: str, category: str, subcategory: str, title: str, content: str):
    try:
        client = get_ollama_client()
        embed_response = client.embeddings(model=EMBED_MODEL, prompt=content)
    except Exception as e:
        logger.exception("Embedding failed while updating record %s", record_id)
        raise RuntimeError(f"Could not generate embedding — record was NOT updated. ({e})") from e

    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE lore_records
        SET project=?, category=?, subcategory=?, title=?, content=?
        WHERE id=?
    ''', (project, category, subcategory, title, content, record_id))
    conn.commit()
    conn.close()

    try:
        collection = get_chroma_collection()
        collection.update(
            ids=[f"rec_{record_id}"],
            embeddings=[embed_response['embedding']],
            documents=[content],
            metadatas=[{"project": project, "category": category, "subcategory": subcategory, "title": title}]
        )
    except Exception as e:
        logger.exception("Chroma update failed for record %s", record_id)
        raise RuntimeError(
            f"Record was updated in the database, but the search index update failed — "
            f"it may return stale results for this record. ({e})"
        ) from e

def delete_record(record_id: int):
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM lore_records WHERE id=?', (record_id,))
    conn.commit()
    conn.close()

    try:
        collection = get_chroma_collection()
        collection.delete(ids=[f"rec_{record_id}"])
    except Exception as e:
        logger.exception("Chroma delete failed for record %s", record_id)
        raise RuntimeError(
            f"Record was deleted from the database, but could not be removed from the search index. ({e})"
        ) from e

# --- STREAMLIT UI ---
init_databases()

st.title("🧠 Cortex")

with st.sidebar:
    st.markdown("### System Status")
    ollama_ok = check_ollama_status(OLLAMA_HOST)
    if ollama_ok:
        st.success("🟢 Ollama connected")
    else:
        st.error("🔴 Ollama unreachable")

    count = get_record_count()
    if count is not None:
        st.metric("Vault Records", count)

    st.divider()
    st.markdown("### Active Models")
    st.markdown(f"**Librarian**")
    st.markdown(f"`{st.session_state.librarian_model}`")
    st.markdown(f"**Creative**")
    st.markdown(f"`{st.session_state.creative_model}`")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["💬 Brain Dump", "🗄️ The Vault", "✨ Creative Generation", "🤖 Chat with Vault", "⚙️ Settings"])

with tab1:
    st.markdown("### Process a New Idea")

    t1_existing_projects = get_distinct_projects()

    proj_mode_t1 = st.radio("Project Type", ["Existing Project", "New Project Idea"], horizontal=True, key="t1_radio")

    if proj_mode_t1 == "Existing Project":
        if not t1_existing_projects:
            st.warning("No existing projects found. Please use 'New Project Idea'.")
            project_input = ""
        else:
            project_input = st.selectbox("Select Project to add to:", t1_existing_projects, key="t1_existing")
    else:
        project_input = st.text_input("New Project Name (Optional)", placeholder="Leave blank to let AI decide...", key="t1_new")

    user_dump = st.text_area("What are you thinking?", height=150, placeholder="Drop your raw notes here...")

    if st.button("Process & Store", type="primary"):
        if user_dump:
            context, ctx_error = get_project_context(user_dump, project_input) if project_input else ("", None)
            if ctx_error:
                st.warning(f"⚠️ Continuing without project context — {ctx_error}")

            stream_container = st.empty()

            parsed_data = process_brain_dump(user_dump, project_input, context, stream_container, st.session_state.librarian_model)

            if "error" in parsed_data:
                st.error(f"Failed to process: {parsed_data['error']}")
            else:
                try:
                    title, cat, final_proj = save_to_databases(parsed_data, project_input)
                    st.success(f"Successfully stored in **{final_proj}**: **{title}** (Category: {cat})")
                except RuntimeError as e:
                    st.error(str(e))
        else:
            st.warning("Please enter some text first.")

with tab2:
    st.markdown("### Database Records")
    try:
        conn = get_sqlite_connection()
        project_list = ["All Projects"] + get_distinct_projects()

        col1, col2 = st.columns([1, 3])
        with col1:
            selected_project = st.selectbox("Filter by Project:", project_list, key="tab2_filter")

        if selected_project == "All Projects":
            query = "SELECT id, timestamp, project, category, subcategory, title, content FROM lore_records ORDER BY id DESC"
            params = ()
        else:
            query = "SELECT id, timestamp, project, category, subcategory, title, content FROM lore_records WHERE project = ? ORDER BY id DESC"
            params = (selected_project,)

        df = pd.read_sql_query(query, conn, params=params)
        conn.close()

        if df.empty:
            st.info("No records found.")
        else:
            st.dataframe(
                df,
                column_config={
                    "id": st.column_config.NumberColumn("ID", width="small"),
                    "timestamp": st.column_config.DatetimeColumn("Created At", format="MMM D, h:mm a"),
                    "project": "Project",
                    "category": "Category",
                    "subcategory": "Subcategory",
                    "title": "Title",
                    "content": st.column_config.TextColumn("Content", width="large")
                },
                hide_index=True,
                width="stretch"
            )

            st.divider()
            st.markdown("### ✏️ Manage Records")
            edit_id = st.selectbox("Select a Record ID to Edit or Delete:", [None] + df['id'].tolist())

            if edit_id:
                record = df[df['id'] == edit_id].iloc[0]
                with st.form(key=f"edit_form_{edit_id}"):
                    e_proj = st.text_input("Project", record['project'])
                    e_cat = st.text_input("Category", record['category'])
                    e_sub = st.text_input("Subcategory", record['subcategory'])
                    e_title = st.text_input("Title", record['title'])
                    e_content = st.text_area("Content", record['content'], height=200)

                    colA, colB = st.columns(2)
                    with colA:
                        update_btn = st.form_submit_button("💾 Save Changes", type="primary")
                    with colB:
                        confirm_delete = st.checkbox("Confirm permanent deletion")
                        delete_btn = st.form_submit_button("🗑️ Delete Record")

                if update_btn:
                    try:
                        update_record(edit_id, e_proj, e_cat, e_sub, e_title, e_content)
                        st.success(f"Record {edit_id} updated successfully!")
                        st.rerun()
                    except RuntimeError as e:
                        st.error(str(e))
                if delete_btn:
                    if not confirm_delete:
                        st.warning("Check 'Confirm permanent deletion' above before deleting.")
                    else:
                        try:
                            delete_record(edit_id)
                            st.warning(f"Record {edit_id} permanently deleted.")
                            st.rerun()
                        except RuntimeError as e:
                            st.error(str(e))

    except Exception as e:
        logger.exception("Error loading database in Vault tab")
        st.error(f"Error loading database: {str(e)}")

with tab3:
    st.markdown("### 🎨 Creative Brainstorming")

    existing_projects = get_distinct_projects()

    proj_mode = st.radio("Project Type", ["Existing Project", "New Project Idea"], horizontal=True, key="t3_radio")

    if proj_mode == "Existing Project":
        if not existing_projects:
            st.warning("No existing projects found. Please use 'New Project Idea'.")
            gen_project = ""
        else:
            gen_project = st.selectbox("Select Project to add to:", existing_projects, key="tab3_existing")
    else:
        gen_project = st.text_input("New Project Name (Optional)", placeholder="Leave blank to let AI decide...", key="tab3_new")

    creative_prompt = st.text_area("What do you want to brainstorm?", height=100)

    if st.button("Generate Ideas"):
        if creative_prompt:
            stream_container = st.empty()
            try:
                context, ctx_error = get_project_context(creative_prompt, gen_project) if gen_project else ("", None)
                if ctx_error:
                    st.warning(f"⚠️ Continuing without project context — {ctx_error}")

                system_instructions = "You are a highly creative brainstorming assistant. Provide detailed, atmospheric, and imaginative concepts based on the user prompt."
                if context:
                    system_instructions += f"\n\nPlease ensure your ideas align with the existing project canon provided below:\n{context}"

                client = get_ollama_client()
                response_stream = client.chat(
                    model=st.session_state.creative_model,
                    messages=[
                        {'role': 'system', 'content': system_instructions},
                        {'role': 'user', 'content': creative_prompt}
                    ],
                    stream=True
                )

                full_response = ""
                for chunk in response_stream:
                    if 'message' in chunk and 'content' in chunk['message']:
                        full_response += chunk['message']['content']
                        stream_container.markdown(full_response + "▌")

                stream_container.empty()
                st.session_state['generated_idea'] = full_response

            except Exception as e:
                logger.exception("Creative generation failed")
                st.error(f"Error communicating with model: {e}")
        else:
            st.warning("Please enter a prompt.")

    if 'generated_idea' in st.session_state:
        st.markdown("---")
        st.markdown("### Generated Concept")
        st.write(st.session_state['generated_idea'])

        if st.button("Vault This Concept", type="primary"):
            structuring_container = st.empty()
            context, ctx_error = get_project_context(st.session_state['generated_idea'], gen_project) if gen_project else ("", None)
            if ctx_error:
                st.warning(f"⚠️ Continuing without project context — {ctx_error}")

            parsed_data = process_brain_dump(st.session_state['generated_idea'], gen_project, context, structuring_container, st.session_state.librarian_model)

            if "error" in parsed_data:
                st.error(f"Failed to process: {parsed_data['error']}")
            else:
                try:
                    title, cat, final_proj = save_to_databases(parsed_data, gen_project)
                    st.success(f"Successfully stored in **{final_proj}**: **{title}** (Category: {cat})")
                    del st.session_state['generated_idea']
                    st.rerun()
                except RuntimeError as e:
                    st.error(str(e))

with tab4:
    st.markdown("### 🤖 Chat with the Vault")
    st.markdown("Ask the Librarian questions about your stored projects. It will search the database to answer.")

    chat_project_list = ["All Projects"] + get_distinct_projects()

    chat_project = st.selectbox("Search Context:", chat_project_list, key="tab4_filter")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("Ask about your projects..."):
        st.chat_message("user").markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("assistant"):
            stream_container = st.empty()
            retrieved_context, ctx_error = get_project_context(prompt, chat_project, n_results=5)
            if ctx_error:
                st.warning(f"⚠️ Answering without vault context — {ctx_error}")

            system_prompt = f"""
            You are the Head Librarian of Cortex. Answer the user's questions based ONLY on the database records provided below.
            If the answer isn't in the records, say you don't have that information in the vault yet.

            {retrieved_context}
            """

            try:
                client = get_ollama_client()
                response_stream = client.chat(
                    model=st.session_state.librarian_model,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': prompt}
                    ],
                    stream=True
                )

                full_response = ""
                for chunk in response_stream:
                    if 'message' in chunk and 'content' in chunk['message']:
                        full_response += chunk['message']['content']
                        stream_container.markdown(full_response + "▌")

                stream_container.markdown(full_response)
                st.session_state.messages.append({"role": "assistant", "content": full_response})

            except Exception as e:
                logger.exception("Chat failed")
                st.error(f"Chat error: {str(e)}")

with tab5:
    st.markdown("### ⚙️ Settings & Model Routing")
    st.markdown("Select which local Ollama models power each function of the vault.")

    if st.button("🔄 Refresh Model List"):
        st.session_state.ollama_models = get_available_models()
        st.rerun()

    lib_idx = st.session_state.ollama_models.index(st.session_state.librarian_model) if st.session_state.librarian_model in st.session_state.ollama_models else 0
    cre_idx = st.session_state.ollama_models.index(st.session_state.creative_model) if st.session_state.creative_model in st.session_state.ollama_models else 0

    new_lib = st.selectbox("Librarian Model (Handles JSON, Archiving, and Chat)", st.session_state.ollama_models, index=lib_idx)
    new_cre = st.selectbox("Creative Model (Handles Brainstorming and Ideation)", st.session_state.ollama_models, index=cre_idx)

    if new_lib != st.session_state.librarian_model or new_cre != st.session_state.creative_model:
        st.session_state.librarian_model = new_lib
        st.session_state.creative_model = new_cre
        st.success("Model routing updated!")
        st.rerun()
