"""
title: Project Workspace
author: Colin Bouchard
version: 3.0.1
description: Create brand-new projects or import existing ZIP/loose files, browse and edit project files, then package and share the complete project as a downloadable ZIP.
requirements:
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import tempfile
import zipfile

from pathlib import Path, PurePosixPath
from typing import Any, Optional


class Tools:
    def __init__(self):
        self.max_zip_size = 250 * 1024 * 1024
        self.max_project_size = 750 * 1024 * 1024
        self.max_file_size = 50 * 1024 * 1024
        self.max_edit_size = 10 * 1024 * 1024
        self.max_members = 10000
        self.max_compression_ratio = 250

        self.workspace_base = (
            Path(tempfile.gettempdir()) / "openwebui_project_workspaces"
        )

        self.workspace_base.mkdir(
            parents=True,
            exist_ok=True,
        )

    # =========================================================
    # Generic helpers
    # =========================================================

    def _result(
        self,
        **kwargs: Any,
    ) -> str:
        return json.dumps(
            kwargs,
            ensure_ascii=False,
            indent=2,
        )

    def _human_size(
        self,
        size: int,
    ) -> str:
        units = [
            "B",
            "KB",
            "MB",
            "GB",
            "TB",
        ]

        value = float(size)

        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"

                return f"{value:.1f} {unit}"

            value /= 1024

        return f"{size} B"

    def _safe_relative_path(
        self,
        value: str,
    ) -> str:
        if not value:
            raise ValueError("Project path cannot be empty.")

        normalized = str(value).replace("\\", "/").strip()

        if not normalized:
            raise ValueError("Project path cannot be empty.")

        if normalized.startswith("/"):
            raise ValueError(f'Absolute paths are not allowed: "{value}"')

        if len(normalized) >= 2 and normalized[1] == ":":
            raise ValueError(f'Drive paths are not allowed: "{value}"')

        path = PurePosixPath(normalized)

        if path.is_absolute():
            raise ValueError(f'Absolute paths are not allowed: "{value}"')

        if ".." in path.parts:
            raise ValueError(f'Path traversal is not allowed: "{value}"')

        clean = str(path)

        if clean in (
            "",
            ".",
        ):
            raise ValueError("Project path cannot be empty.")

        return clean

    def _safe_project_name(
        self,
        name: str,
    ) -> str:
        if not name:
            return "project"

        cleaned = str(name).strip().replace("\\", "-").replace("/", "-")

        cleaned = "".join(
            char if (char.isalnum() or char in "-_. ") else "-" for char in cleaned
        )

        cleaned = cleaned.strip(" .-_")

        return cleaned or "project"

    # =========================================================
    # Workspace scoping
    # =========================================================

    def _user_id(
        self,
        __user__: Any,
    ) -> str:
        if isinstance(
            __user__,
            dict,
        ):
            value = __user__.get("id")

            if value:
                return str(value)

        value = getattr(
            __user__,
            "id",
            None,
        )

        if value:
            return str(value)

        return "anonymous"

    def _scope_key(
        self,
        __chat_id__: Optional[str],
        __user__: Any,
    ) -> str:
        user_id = self._user_id(__user__)

        chat_id = str(__chat_id__) if __chat_id__ else "no-chat"

        raw = f"{user_id}:{chat_id}"

        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _scope_dir(
        self,
        __chat_id__: Optional[str],
        __user__: Any,
    ) -> Path:
        path = self.workspace_base / self._scope_key(
            __chat_id__,
            __user__,
        )

        path.mkdir(
            parents=True,
            exist_ok=True,
        )

        return path

    def _workspace_dir(
        self,
        __chat_id__: Optional[str],
        __user__: Any,
    ) -> Path:
        return (
            self._scope_dir(
                __chat_id__,
                __user__,
            )
            / "workspace"
        )

    def _manifest_path(
        self,
        __chat_id__: Optional[str],
        __user__: Any,
    ) -> Path:
        return (
            self._scope_dir(
                __chat_id__,
                __user__,
            )
            / "manifest.json"
        )

    # =========================================================
    # Manifest
    # =========================================================

    def _load_manifest(
        self,
        __chat_id__: Optional[str],
        __user__: Any,
    ) -> dict[str, Any]:
        manifest_path = self._manifest_path(
            __chat_id__,
            __user__,
        )

        if not manifest_path.exists():
            raise ValueError(
                "No active project workspace exists. "
                "Use create_project or open_zip_project first."
            )

        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))

        except Exception as exc:
            raise RuntimeError("The project workspace manifest is invalid.") from exc

    def _save_manifest(
        self,
        manifest: dict[str, Any],
        __chat_id__: Optional[str],
        __user__: Any,
    ) -> None:
        self._manifest_path(
            __chat_id__,
            __user__,
        ).write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # =========================================================
    # File hashing / snapshots
    # =========================================================

    def _hash_file(
        self,
        path: Path,
    ) -> str:
        digest = hashlib.sha256()

        with path.open("rb") as handle:
            while True:
                block = handle.read(1024 * 1024)

                if not block:
                    break

                digest.update(block)

        return digest.hexdigest()

    def _snapshot(
        self,
        root: Path,
    ) -> dict[str, str]:
        result: dict[str, str] = {}

        if not root.exists():
            return result

        for path in root.rglob("*"):
            if not path.is_file():
                continue

            relative = path.relative_to(root).as_posix()

            result[relative] = self._hash_file(path)

        return result

    def _project_size(
        self,
        root: Path,
    ) -> int:
        total = 0

        if not root.exists():
            return 0

        for path in root.rglob("*"):
            if not path.is_file():
                continue

            total += path.stat().st_size

            if total > self.max_project_size:
                raise ValueError("Project exceeds the configured maximum size.")

        return total

    # =========================================================
    # Attachment helpers
    # =========================================================

    def _get_attachment_path(
        self,
        file_obj: Any,
    ) -> Optional[str]:
        if file_obj is None:
            return None

        if isinstance(
            file_obj,
            str,
        ):
            if os.path.isfile(file_obj):
                return file_obj

            return None

        if isinstance(
            file_obj,
            dict,
        ):
            candidates = [
                file_obj.get("path"),
                file_obj.get("file_path"),
                file_obj.get("filepath"),
            ]

            nested = file_obj.get("file")

            if isinstance(
                nested,
                dict,
            ):
                candidates.extend(
                    [
                        nested.get("path"),
                        nested.get("file_path"),
                        nested.get("filepath"),
                    ]
                )

                data = nested.get("data")

                if isinstance(
                    data,
                    dict,
                ):
                    candidates.extend(
                        [
                            data.get("path"),
                            data.get("file_path"),
                            data.get("filepath"),
                        ]
                    )

            data = file_obj.get("data")

            if isinstance(
                data,
                dict,
            ):
                candidates.extend(
                    [
                        data.get("path"),
                        data.get("file_path"),
                        data.get("filepath"),
                    ]
                )

            for candidate in candidates:
                if candidate and os.path.isfile(str(candidate)):
                    return str(candidate)

        for attribute in (
            "path",
            "file_path",
            "filepath",
        ):
            candidate = getattr(
                file_obj,
                attribute,
                None,
            )

            if candidate and os.path.isfile(str(candidate)):
                return str(candidate)

        return None

    def _attachment_name(
        self,
        file_obj: Any,
    ) -> Optional[str]:
        if isinstance(
            file_obj,
            dict,
        ):
            for key in (
                "name",
                "filename",
            ):
                value = file_obj.get(key)

                if value:
                    return os.path.basename(str(value))

            nested = file_obj.get("file")

            if isinstance(
                nested,
                dict,
            ):
                for key in (
                    "name",
                    "filename",
                ):
                    value = nested.get(key)

                    if value:
                        return os.path.basename(str(value))

        path = self._get_attachment_path(file_obj)

        if path:
            return os.path.basename(path)

        return None

    # =========================================================
    # Workspace creation
    # =========================================================

    def _reset_workspace(
        self,
        __chat_id__: Optional[str],
        __user__: Any,
    ) -> Path:
        workspace = self._workspace_dir(
            __chat_id__,
            __user__,
        )

        if workspace.exists():
            shutil.rmtree(
                workspace,
                ignore_errors=True,
            )

        workspace.mkdir(
            parents=True,
            exist_ok=True,
        )

        return workspace

    async def create_project(
        self,
        project_name: str,
        import_attached_files: bool = False,
        __files__: Optional[list[Any]] = None,
        __chat_id__: Optional[str] = None,
        __user__: Any = None,
    ) -> str:
        """
        Create a brand-new project workspace.

        Use this when there is no existing ZIP project.

        If import_attached_files=true, loose attached files are
        copied into the new project. ZIP attachments are skipped.

        :param project_name: Project name.
        :param import_attached_files: Import loose attached files.
        """

        try:
            name = self._safe_project_name(project_name)

            workspace = self._reset_workspace(
                __chat_id__,
                __user__,
            )

            imported: list[str] = []

            if import_attached_files and __files__:
                for file_obj in __files__:
                    source = self._get_attachment_path(file_obj)

                    if not source:
                        continue

                    filename = self._attachment_name(file_obj) or os.path.basename(
                        source
                    )

                    if filename.lower().endswith(".zip"):
                        continue

                    safe_name = self._safe_relative_path(os.path.basename(filename))

                    size = os.path.getsize(source)

                    if size > self.max_file_size:
                        raise ValueError(
                            f'Attachment "{filename}" exceeds '
                            "the maximum individual file size."
                        )

                    target = workspace / safe_name

                    target.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    shutil.copy2(
                        source,
                        target,
                    )

                    imported.append(safe_name)

            baseline = self._snapshot(workspace)

            manifest = {
                "project_name": name,
                "source_type": ("loose_attachments" if imported else "new"),
                "source_name": None,
                "baseline": baseline,
            }

            self._save_manifest(
                manifest,
                __chat_id__,
                __user__,
            )

            return self._result(
                status="success",
                project_name=name,
                source_type=manifest["source_type"],
                imported_files=imported,
                file_count=len(baseline),
                message=(
                    "Project workspace created. "
                    "Use write_project_file to add project files."
                ),
            )

        except Exception as exc:
            return self._result(
                status="error",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # =========================================================
    # Open ZIP project
    # =========================================================

    async def open_zip_project(
        self,
        archive_name: Optional[str] = None,
        project_name: Optional[str] = None,
        __files__: Optional[list[Any]] = None,
        __chat_id__: Optional[str] = None,
        __user__: Any = None,
    ) -> str:
        """
        Import an attached ZIP archive into a project workspace.

        :param archive_name: Optional ZIP name when several ZIPs are attached.
        :param project_name: Optional project name.
        """

        try:
            if not __files__:
                raise ValueError("No files are attached.")

            candidates: list[tuple[str, str]] = []

            for file_obj in __files__:
                source = self._get_attachment_path(file_obj)

                if not source:
                    continue

                display_name = self._attachment_name(file_obj) or os.path.basename(
                    source
                )

                if display_name.lower().endswith(".zip") or source.lower().endswith(
                    ".zip"
                ):
                    candidates.append(
                        (
                            source,
                            display_name,
                        )
                    )

            if not candidates:
                raise ValueError("No attached ZIP archive was found.")

            selected: Optional[tuple[str, str]] = None

            if archive_name:
                wanted = archive_name.strip().lower()

                for item in candidates:
                    if item[1].lower() == wanted:
                        selected = item
                        break

                if selected is None:
                    for item in candidates:
                        if wanted in item[1].lower():
                            selected = item
                            break

            else:
                selected = candidates[0]

            if selected is None:
                raise ValueError(f'Could not find ZIP matching "{archive_name}".')

            source_path, display_name = selected

            if os.path.getsize(source_path) > self.max_zip_size:
                raise ValueError("ZIP exceeds maximum archive size.")

            workspace = self._reset_workspace(
                __chat_id__,
                __user__,
            )

            count = 0
            total_size = 0

            try:
                archive = zipfile.ZipFile(
                    source_path,
                    "r",
                )

            except zipfile.BadZipFile as exc:
                raise ValueError("Attached ZIP is invalid.") from exc

            with archive:
                infos = archive.infolist()

                if len(infos) > self.max_members:
                    raise ValueError("ZIP contains too many entries.")

                for info in infos:
                    if info.is_dir():
                        continue

                    safe_name = self._safe_relative_path(info.filename)

                    total_size += info.file_size

                    if total_size > self.max_project_size:
                        raise ValueError(
                            "Uncompressed project exceeds " "the maximum project size."
                        )

                    if info.compress_size > 0 and info.file_size > 0:
                        ratio = info.file_size / info.compress_size

                        if ratio > self.max_compression_ratio:
                            raise ValueError(
                                "Suspicious compression ratio " f'in "{safe_name}".'
                            )

                    target = workspace / safe_name

                    target.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    with archive.open(
                        info,
                        "r",
                    ) as source_handle:
                        with target.open("wb") as target_handle:
                            shutil.copyfileobj(
                                source_handle,
                                target_handle,
                                length=1024 * 1024,
                            )

                    count += 1

            baseline = self._snapshot(workspace)

            name = self._safe_project_name(project_name or Path(display_name).stem)

            manifest = {
                "project_name": name,
                "source_type": "zip",
                "source_name": display_name,
                "baseline": baseline,
            }

            self._save_manifest(
                manifest,
                __chat_id__,
                __user__,
            )

            return self._result(
                status="success",
                project_name=name,
                source_type="zip",
                source_name=display_name,
                file_count=count,
                message=("ZIP project imported into the workspace."),
            )

        except Exception as exc:
            return self._result(
                status="error",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # =========================================================
    # Import loose files
    # =========================================================

    async def import_attached_files(
        self,
        destination_directory: str = "",
        overwrite: bool = False,
        __files__: Optional[list[Any]] = None,
        __chat_id__: Optional[str] = None,
        __user__: Any = None,
    ) -> str:
        """
        Import loose attached files into the active project.

        ZIP attachments are skipped.
        """

        try:
            self._load_manifest(
                __chat_id__,
                __user__,
            )

            workspace = self._workspace_dir(
                __chat_id__,
                __user__,
            )

            destination = (
                self._safe_relative_path(destination_directory)
                if destination_directory
                else ""
            )

            imported: list[str] = []
            skipped: list[str] = []

            for file_obj in __files__ or []:
                source = self._get_attachment_path(file_obj)

                if not source:
                    continue

                filename = self._attachment_name(file_obj) or os.path.basename(source)

                if filename.lower().endswith(".zip"):
                    skipped.append(filename)
                    continue

                relative = (
                    f"{destination}/" f"{os.path.basename(filename)}"
                    if destination
                    else os.path.basename(filename)
                )

                relative = self._safe_relative_path(relative)

                target = workspace / relative

                if target.exists() and not overwrite:
                    skipped.append(relative)
                    continue

                target.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                shutil.copy2(
                    source,
                    target,
                )

                imported.append(relative)

            return self._result(
                status="success",
                imported=imported,
                skipped=skipped,
            )

        except Exception as exc:
            return self._result(
                status="error",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # =========================================================
    # List files
    # =========================================================

    async def list_project_files(
        self,
        filter_pattern: Optional[str] = None,
        __chat_id__: Optional[str] = None,
        __user__: Any = None,
    ) -> str:
        """
        List files in the active project.

        Supports patterns such as:
        *.py
        *.yaml
        docker/*
        src/*
        **/*.json
        """

        try:
            manifest = self._load_manifest(
                __chat_id__,
                __user__,
            )

            workspace = self._workspace_dir(
                __chat_id__,
                __user__,
            )

            files: list[dict[str, Any]] = []

            for path in workspace.rglob("*"):
                if not path.is_file():
                    continue

                relative = path.relative_to(workspace).as_posix()

                if filter_pattern:
                    pattern = filter_pattern.replace("\\", "/")

                    if not (
                        fnmatch.fnmatch(
                            relative,
                            pattern,
                        )
                        or fnmatch.fnmatch(
                            path.name,
                            pattern,
                        )
                    ):
                        continue

                files.append(
                    {
                        "path": relative,
                        "size_bytes": (path.stat().st_size),
                    }
                )

            files.sort(key=lambda item: (item["path"].lower()))

            return self._result(
                status="success",
                project_name=manifest["project_name"],
                filter_pattern=filter_pattern,
                count=len(files),
                files=files,
            )

        except Exception as exc:
            return self._result(
                status="error",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # =========================================================
    # Read file
    # =========================================================

    async def read_project_file(
        self,
        file_path: str,
        start_line: int = 1,
        max_lines: int = 500,
        __chat_id__: Optional[str] = None,
        __user__: Any = None,
    ) -> str:
        """
        Read a text file from the active project.
        """

        try:
            self._load_manifest(
                __chat_id__,
                __user__,
            )

            relative = self._safe_relative_path(file_path)

            workspace = self._workspace_dir(
                __chat_id__,
                __user__,
            )

            target = workspace / relative

            if not target.is_file():
                raise ValueError(f'Project file "{relative}" does not exist.')

            size = target.stat().st_size

            if size > self.max_file_size:
                raise ValueError(f'File "{relative}" is too large to read.')

            data = target.read_bytes()

            if b"\x00" in data[:8192]:
                return self._result(
                    status="binary",
                    path=relative,
                    size_bytes=size,
                    message=("File appears to be binary."),
                )

            text: Optional[str] = None
            encoding_used: Optional[str] = None

            for encoding in (
                "utf-8",
                "utf-8-sig",
                "utf-16",
                "latin-1",
            ):
                try:
                    text = data.decode(encoding)

                    encoding_used = encoding

                    break

                except UnicodeDecodeError:
                    continue

            if text is None:
                text = data.decode(
                    "utf-8",
                    errors="replace",
                )

                encoding_used = "utf-8-replace"

            lines = text.splitlines()

            start_line = max(
                1,
                int(start_line),
            )

            max_lines = max(
                1,
                min(
                    int(max_lines),
                    2000,
                ),
            )

            start_index = min(
                start_line - 1,
                len(lines),
            )

            end_index = min(
                start_index + max_lines,
                len(lines),
            )

            content = "\n".join(lines[start_index:end_index])

            return self._result(
                status="success",
                path=relative,
                encoding=encoding_used,
                total_lines=len(lines),
                start_line=start_line,
                end_line=end_index,
                has_more=(end_index < len(lines)),
                next_start_line=(end_index + 1 if end_index < len(lines) else None),
                content=content,
            )

        except Exception as exc:
            return self._result(
                status="error",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # =========================================================
    # Write/create file
    # =========================================================

    async def write_project_file(
        self,
        file_path: str,
        content: str,
        overwrite: bool = True,
        __chat_id__: Optional[str] = None,
        __user__: Any = None,
    ) -> str:
        """
        Create or replace a UTF-8 text file in the active project.

        content must contain the complete final file.
        """

        try:
            manifest = self._load_manifest(
                __chat_id__,
                __user__,
            )

            relative = self._safe_relative_path(file_path)

            encoded = content.encode("utf-8")

            if len(encoded) > self.max_edit_size:
                raise ValueError("File content exceeds the maximum " "text edit size.")

            workspace = self._workspace_dir(
                __chat_id__,
                __user__,
            )

            target = workspace / relative

            existed = target.exists()

            if existed and not overwrite:
                raise ValueError(f'File "{relative}" already exists.')

            target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            target.write_bytes(encoded)

            self._project_size(workspace)

            return self._result(
                status="success",
                project_name=manifest["project_name"],
                action=("replace" if existed else "create"),
                path=relative,
                size_bytes=len(encoded),
                message=("Project file written successfully."),
            )

        except Exception as exc:
            return self._result(
                status="error",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # =========================================================
    # Delete
    # =========================================================

    async def delete_project_file(
        self,
        file_path: str,
        __chat_id__: Optional[str] = None,
        __user__: Any = None,
    ) -> str:
        """
        Delete a file from the active project.
        """

        try:
            self._load_manifest(
                __chat_id__,
                __user__,
            )

            relative = self._safe_relative_path(file_path)

            workspace = self._workspace_dir(
                __chat_id__,
                __user__,
            )

            target = workspace / relative

            if not target.is_file():
                raise ValueError(f'File "{relative}" does not exist.')

            target.unlink()

            parent = target.parent

            while parent != workspace and parent.exists():
                try:
                    parent.rmdir()

                except OSError:
                    break

                parent = parent.parent

            return self._result(
                status="success",
                action="delete",
                path=relative,
            )

        except Exception as exc:
            return self._result(
                status="error",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # =========================================================
    # Rename
    # =========================================================

    async def rename_project_file(
        self,
        old_path: str,
        new_path: str,
        overwrite: bool = False,
        __chat_id__: Optional[str] = None,
        __user__: Any = None,
    ) -> str:
        """
        Rename or move a project file.
        """

        try:
            self._load_manifest(
                __chat_id__,
                __user__,
            )

            old_relative = self._safe_relative_path(old_path)

            new_relative = self._safe_relative_path(new_path)

            workspace = self._workspace_dir(
                __chat_id__,
                __user__,
            )

            source = workspace / old_relative

            destination = workspace / new_relative

            if not source.is_file():
                raise ValueError(f'File "{old_relative}" does not exist.')

            if destination.exists() and not overwrite:
                raise ValueError(f'File "{new_relative}" already exists.')

            destination.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            if destination.exists() and overwrite:
                if destination.is_file():
                    destination.unlink()

                else:
                    shutil.rmtree(destination)

            shutil.move(
                str(source),
                str(destination),
            )

            return self._result(
                status="success",
                action="rename",
                old_path=old_relative,
                new_path=new_relative,
            )

        except Exception as exc:
            return self._result(
                status="error",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # =========================================================
    # Project status
    # =========================================================

    async def show_project_status(
        self,
        __chat_id__: Optional[str] = None,
        __user__: Any = None,
    ) -> str:
        """
        Show modifications relative to the original baseline.

        For brand-new projects all created files appear as added.
        """

        try:
            manifest = self._load_manifest(
                __chat_id__,
                __user__,
            )

            workspace = self._workspace_dir(
                __chat_id__,
                __user__,
            )

            baseline = manifest.get(
                "baseline",
                {},
            )

            current = self._snapshot(workspace)

            baseline_names = set(baseline.keys())

            current_names = set(current.keys())

            added = sorted(current_names - baseline_names)

            deleted = sorted(baseline_names - current_names)

            modified = sorted(
                name
                for name in (baseline_names & current_names)
                if (baseline[name] != current[name])
            )

            unchanged = sorted(
                name
                for name in (baseline_names & current_names)
                if (baseline[name] == current[name])
            )

            return self._result(
                status="success",
                project_name=manifest["project_name"],
                source_type=manifest["source_type"],
                source_name=manifest.get("source_name"),
                file_count=len(current),
                project_size_bytes=(self._project_size(workspace)),
                added=added,
                modified=modified,
                deleted=deleted,
                unchanged_count=len(unchanged),
                change_count=(len(added) + len(modified) + len(deleted)),
                ready_to_package=bool(current),
            )

        except Exception as exc:
            return self._result(
                status="error",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # =========================================================
    # Clear project
    # =========================================================

    async def clear_project(
        self,
        __chat_id__: Optional[str] = None,
        __user__: Any = None,
    ) -> str:
        """
        Delete the current temporary project workspace.

        Only use when intentionally discarding the current project.
        """

        try:
            scope = self._scope_dir(
                __chat_id__,
                __user__,
            )

            if scope.exists():
                shutil.rmtree(
                    scope,
                    ignore_errors=True,
                )

            return self._result(
                status="success",
                message=("Project workspace cleared."),
            )

        except Exception as exc:
            return self._result(
                status="error",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    # =========================================================
    # ZIP creation
    # =========================================================

    def _build_project_zip(
        self,
        workspace: Path,
        output_path: Path,
    ) -> dict[str, Any]:
        files: list[str] = []

        with zipfile.ZipFile(
            output_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for path in sorted(workspace.rglob("*")):
                if not path.is_file():
                    continue

                relative = path.relative_to(workspace).as_posix()

                safe_name = self._safe_relative_path(relative)

                archive.write(
                    path,
                    arcname=safe_name,
                )

                files.append(safe_name)

        return {
            "file_count": len(files),
            "files": files,
        }

    # =========================================================
    # Open WebUI upload
    # =========================================================

    def _make_user_model(
        self,
        __user__: Any,
    ) -> Any:
        try:
            from open_webui.models.users import (
                UserModel,
            )

        except Exception:
            return __user__

        if isinstance(
            __user__,
            dict,
        ):
            try:
                return UserModel(**__user__)

            except Exception:
                return __user__

        return __user__

    async def _upload_generated_zip(
        self,
        zip_path: Path,
        filename: str,
        __request__: Any,
        __user__: Any,
        __event_emitter__: Any,
    ) -> dict[str, Any]:
        if __request__ is None:
            raise RuntimeError("Open WebUI request context is unavailable.")

        if not __user__:
            raise RuntimeError("Open WebUI user context is unavailable.")

        try:
            from fastapi import UploadFile
            from starlette.datastructures import (
                Headers,
            )

            from open_webui.routers.files import (
                upload_file_handler,
            )

        except Exception as exc:
            raise RuntimeError(
                "Could not import Open WebUI file upload components."
            ) from exc

        user = self._make_user_model(__user__)

        file_handle = zip_path.open("rb")

        upload = UploadFile(
            filename=filename,
            file=file_handle,
            headers=Headers({"content-type": ("application/zip")}),
        )

        try:
            try:
                result = await upload_file_handler(
                    __request__,
                    file=upload,
                    metadata={},
                    process=False,
                    process_in_background=False,
                    user=user,
                )

            except TypeError:
                # Compatibility fallback for Open WebUI
                # builds whose handler does not expose
                # process_in_background.
                result = await upload_file_handler(
                    __request__,
                    file=upload,
                    metadata={},
                    process=False,
                    user=user,
                )

        finally:
            try:
                await upload.close()

            except Exception:
                try:
                    file_handle.close()

                except Exception:
                    pass

        if isinstance(
            result,
            dict,
        ):
            file_data = result

        elif hasattr(
            result,
            "model_dump",
        ):
            file_data = result.model_dump()

        else:
            file_data = {
                "id": getattr(
                    result,
                    "id",
                    None,
                ),
                "filename": getattr(
                    result,
                    "filename",
                    None,
                ),
            }

        file_id = file_data.get("id")

        returned_filename = file_data.get("filename") or filename

        if not file_id:
            raise RuntimeError(
                "Open WebUI did not return a file ID " "for the generated ZIP."
            )

        try:
            content_url = str(
                __request__.app.url_path_for(
                    "get_file_content_by_id",
                    id=file_id,
                )
            )

        except Exception:
            content_url = f"/api/v1/files/" f"{file_id}/content"

        separator = "&" if "?" in content_url else "?"

        download_url = f"{content_url}" f"{separator}attachment=true"

        emitted = False

        if __event_emitter__:
            entry = {
                "type": "file",
                "url": content_url,
                "name": returned_filename,
            }

            try:
                await __event_emitter__(
                    {
                        "type": ("chat:message:files"),
                        "data": {"files": [entry]},
                    }
                )

                emitted = True

            except Exception:
                try:
                    await __event_emitter__(
                        {
                            "type": "files",
                            "data": {"files": [entry]},
                        }
                    )

                    emitted = True

                except Exception:
                    emitted = False

        return {
            "file_id": file_id,
            "filename": returned_filename,
            "content_url": content_url,
            "download_url": download_url,
            "emitted_to_chat": emitted,
        }

    # =========================================================
    # FINAL PACKAGE TOOL
    # =========================================================

    async def package_project_zip(
        self,
        output_name: Optional[str] = None,
        __chat_id__: Optional[str] = None,
        __user__: Any = None,
        __request__: Any = None,
        __event_emitter__: Any = None,
    ) -> str:
        """
        Package the complete active project into a downloadable ZIP.

        Works for:
        - brand-new projects,
        - projects imported from ZIP,
        - projects built from loose attached files.

        This is the finalization tool.

        A project creation/editing task is not complete until
        this returns status="success" and a download_url.

        The assistant must use download_url exactly in the
        final Markdown download link.
        """

        temp_dir: Optional[Path] = None

        try:
            manifest = self._load_manifest(
                __chat_id__,
                __user__,
            )

            workspace = self._workspace_dir(
                __chat_id__,
                __user__,
            )

            snapshot = self._snapshot(workspace)

            if not snapshot:
                raise ValueError(
                    "The project workspace is empty. "
                    "Create project files before packaging."
                )

            project_size = self._project_size(workspace)

            if output_name:
                filename = os.path.basename(output_name.strip())

                if not filename:
                    raise ValueError("Output ZIP filename is empty.")

                if not filename.lower().endswith(".zip"):
                    filename += ".zip"

            else:
                filename = f"{manifest['project_name']}.zip"

            if len(filename) > 180:
                filename = Path(filename).stem[:170] + ".zip"

            if __event_emitter__:
                try:
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": ("Packaging project..."),
                                "done": False,
                            },
                        }
                    )

                except Exception:
                    pass

            temp_dir = Path(tempfile.mkdtemp(prefix=("openwebui_project_zip_")))

            output_path = temp_dir / filename

            build = self._build_project_zip(
                workspace,
                output_path,
            )

            zip_size = output_path.stat().st_size

            if zip_size > self.max_zip_size:
                raise ValueError("Generated ZIP exceeds maximum size.")

            with zipfile.ZipFile(
                output_path,
                "r",
            ) as archive:
                bad_member = archive.testzip()

                if bad_member is not None:
                    raise RuntimeError(
                        "Generated ZIP validation failed " f"at member: {bad_member}"
                    )

            if __event_emitter__:
                try:
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": ("Uploading project ZIP..."),
                                "done": False,
                            },
                        }
                    )

                except Exception:
                    pass

            upload = await self._upload_generated_zip(
                output_path,
                filename,
                __request__,
                __user__,
                __event_emitter__,
            )

            status_data = json.loads(
                await self.show_project_status(
                    __chat_id__=__chat_id__,
                    __user__=__user__,
                )
            )

            if __event_emitter__:
                try:
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": ("Project ZIP ready."),
                                "done": True,
                            },
                        }
                    )

                except Exception:
                    pass

            return self._result(
                status="success",
                artifact_created=True,
                task_finalized=True,
                project_name=manifest["project_name"],
                source_type=manifest["source_type"],
                filename=upload["filename"],
                file_id=upload["file_id"],
                download_url=upload["download_url"],
                content_url=upload["content_url"],
                emitted_to_chat=upload["emitted_to_chat"],
                project_file_count=build["file_count"],
                project_size_bytes=project_size,
                zip_size_bytes=zip_size,
                added=status_data.get(
                    "added",
                    [],
                ),
                modified=status_data.get(
                    "modified",
                    [],
                ),
                deleted=status_data.get(
                    "deleted",
                    [],
                ),
                final_response_requirement=(
                    "The project ZIP was successfully created. "
                    "The assistant MUST include a clickable "
                    "Markdown link using download_url EXACTLY "
                    "as returned."
                ),
            )

        except Exception as exc:
            if __event_emitter__:
                try:
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": ("Project packaging failed."),
                                "done": True,
                            },
                        }
                    )

                except Exception:
                    pass

            return self._result(
                status="error",
                artifact_created=False,
                task_finalized=False,
                error_type=type(exc).__name__,
                error=str(exc),
                download_url=None,
                final_response_requirement=(
                    "Do not claim the project ZIP was created "
                    "and do not invent a download URL."
                ),
            )

        finally:
            if temp_dir:
                shutil.rmtree(
                    temp_dir,
                    ignore_errors=True,
                )
