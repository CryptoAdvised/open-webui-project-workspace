# open-webui-project-workspace
# Project Workspace Tools & AI Workflow

> Complete documentation, system prompt guidelines, and developer reference for the **Project Workspace Tools** Open WebUI extension (`v3.0.1`).

---

## Technical Overview

The **Project Workspace Tools** extension equips Open WebUI LLMs with full-stack file workspace capabilities. It replaces piecemeal code block suggestions with end-to-end execution. Models can initialize project environments, import archives or loose script attachments, perform CRUD file operations, track workspace mutation baselines, and package the completed project into a downloadable `.zip` file.

### Key Capabilities

* **Stateful Workspace Isolation:** Scoped per user and chat session using SHA-256 session digests in isolated temporary subdirectories.
* **Dynamic Project Ingestion:** Ingests greenfield projects, uploaded `.zip` archives, or loose code/config attachments (`.py`, `.js`, `.json`, `.yaml`, `Dockerfile`, etc.).
* **Atomic File Operations:** Read, write, rename, filter-list, and delete workspace files safely.
* **Integrity Baseline Tracking:** Tracks snapshot states to report modifications, added files, and deleted items.
* **Secure Packaging:** Built-in safeguards against ZIP bombs, path traversal (`../`), drive letters, absolute paths, and excessive compression ratios.

---

## Tool API Reference

### 1. `create_project`

Initializes a new, empty workspace directory or creates one populated with loose attached files.

```python
async def create_project(
    project_name: str,
    import_attached_files: bool = False
) -> str

```

* **`project_name`**: Target name for the project workspace.
* **`import_attached_files`**: When `True`, non-ZIP loose attached files are copied into the root workspace automatically.

---

### 2. `open_zip_project`

Extracts an attached `.zip` file into the active workspace, setting up a modification baseline.

```python
async def open_zip_project(
    archive_name: Optional[str] = None,
    project_name: Optional[str] = None
) -> str

```

* **`archive_name`**: Optional substring or filename to target a specific ZIP when multiple are attached.
* **`project_name`**: Override default workspace name derived from the ZIP filename.

---

### 3. `import_attached_files`

Copies loose attached files into an existing, active project workspace.

```python
async def import_attached_files(
    destination_directory: str = "",
    overwrite: bool = False
) -> str

```

* **`destination_directory`**: Subdirectory relative path to place loose files.
* **`overwrite`**: Overwrite existing files if filenames match.

---

### 4. `list_project_files`

Returns a list of all workspace files with sizes and paths.

```python
async def list_project_files(
    filter_pattern: Optional[str] = None
) -> str

```

* **`filter_pattern`**: Wildcard glob filter (e.g., `*.py`, `docker/*`, `**/*.json`).

---

### 5. `read_project_file`

Reads the content of a specific workspace file.

```python
async def read_project_file(
    path: str,
    start_line: Optional[int] = None,
    max_lines: Optional[int] = None
) -> str

```

* **`path`**: Relative path of the target file.
* **`start_line` / `max_lines**`: Pagination controls for reading large files.

---

### 6. `write_project_file`

Creates a new file or completely replaces an existing file in the project.

```python
async def write_project_file(
    path: str,
    content: str
) -> str

```

* **`path`**: Target relative file path (creates missing parent directories automatically).
* **`content`**: **Complete** file content. Partial diffs or placeholders (`...`) are strictly prohibited.

---

### 7. `delete_project_file`

Deletes a file or directory from the workspace.

```python
async def delete_project_file(
    path: str
) -> str

```

---

### 8. `rename_project_file`

Renames or moves a file within the workspace.

```python
async def rename_project_file(
    source_path: str,
    destination_path: str
) -> str

```

---

### 9. `show_project_status`

Generates a diff summary comparing current workspace files against the baseline snapshot.

```python
async def show_project_status() -> str

```

---

### 10. `package_project_zip`

Compresses the current workspace directory into a downloadable `.zip` file artifact.

```python
async def package_project_zip(
    output_filename: Optional[str] = None
) -> str

```

* **`output_filename`**: Name for the resulting archive.
* **Returns:** A JSON string containing `status`, `artifact_created`, `task_finalized`, and `download_url`.

---

### 11. `clear_project`

Resets and deletes the active session workspace and manifest.

```python
async def clear_project() -> str

```

---

## AI Agent Decision Matrix

When handling user requests, determine the input context to choose the workflow path:

| Input Scenario | Trigger Example | Initial Tool Call | Workflow |
| --- | --- | --- | --- |
| **Situation A: Greenfield** | *"Create a FastAPI app"* | `create_project(name)` | Initialize workspace $\rightarrow$ Write files $\rightarrow$ Status check $\rightarrow$ Package ZIP |
| **Situation B: Existing ZIP** | Attached `.zip` file | `open_zip_project()` | Import ZIP $\rightarrow$ Inspect/Read files $\rightarrow$ Modify $\rightarrow$ Package ZIP |
| **Situation C: Loose Files** | Attached `.py` or `.yaml` file | `create_project(name, import_attached_files=True)` | Ingest attached files $\rightarrow$ Fix/Modify $\rightarrow$ Status check $\rightarrow$ Package ZIP |

---

## Standard Executable Workflows

### Greenfield Creation Workflow

```
[User Request] 
      │
      ▼
create_project(project_name)
      │
      ▼
write_project_file(file1) ──► write_project_file(file2) ...
      │
      ▼
list_project_files() ──► show_project_status()
      │
      ▼
package_project_zip() ──► Return Download Link

```

### Full Fix / Project Repair Workflow

```
[Attached File / ZIP + "Full Fix" Trigger]
      │
      ├───────────────────────────────┐
      ▼                               ▼
open_zip_project()         create_project(import_attached_files=True)
  (If ZIP attached)           (If loose files attached)
      │                               │
      └───────────────┬───────────────┘
                      ▼
             list_project_files()
                      │
                      ▼
            read_project_file()
                      │
                      ▼
 write_project_file() (complete replacement)
                      │
                      ▼
            show_project_status()
                      │
                      ▼
             package_project_zip()

```

---

## Security Guardrails & Limits

| Parameter | Limit | Enforcement Mechanism |
| --- | --- | --- |
| **Max ZIP Input Size** | 250 MB | Rejects archive extraction if exceeded |
| **Max Uncompressed Project Size** | 750 MB | Real-time byte tracking on import/write |
| **Max Single File Size** | 50 MB | Checked during import and file creation |
| **Max Archive Members** | 10,000 files | Zip bomb prevention check |
| **Max Compression Ratio** | 250:1 | Checked per archive entry during extraction |
| **Path Traversal Protection** | Active | Blocks `..`, drive letters (`C:`), and absolute paths (`/`) |

---

## Installation & Setup

1. Copy `project_workspace_tools.py` into your Open WebUI **Tools** directory or import it via the Open WebUI Admin Panel (**Admin Panel $\rightarrow$ Tools $\rightarrow$ Import**).
2. Ensure the system context / system prompt includes the **Project Builder System Prompt** directives.
3. Verify directory permissions for the host system's temporary directory (`/tmp/openwebui_project_workspaces` or system default).
