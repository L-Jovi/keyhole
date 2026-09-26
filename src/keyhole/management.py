"""Local-only authorization changes and fail-closed native restarts."""

import os
from pathlib import Path
from uuid import uuid4

from .errors import KeyholeError, require
from .filesystem import SafeFS, absolute_directory, canonical_path, identity
from .policy import check_root, normalize_pattern, policy_summary
from .state import StateStore, boot_id, now, validate_state


class Manager:
    def __init__(self, store: StateStore, runtime):
        self.store, self.runtime = store, runtime

    def current(self) -> dict:
        state = self.store.read(missing=True)
        if state["boot_id"] != boot_id():
            for item in state["workspaces"]:
                item["enabled"] = False
        return state

    def transition(self, state: dict) -> dict:
        state["generation"], state["boot_id"], state["updated_at"] = str(uuid4()), boot_id(), now()
        validate_state(state)
        # First invalidate every old request, including those currently parsing.
        self.store.write(state)
        try:
            stopped = self.runtime.stop()
        except Exception:
            # A surviving old runtime must not leave new grants marked active.
            for item in state["workspaces"]:
                item["enabled"] = False
            state["generation"], state["updated_at"] = str(uuid4()), now()
            self.store.write(state)
            raise
        if not any(w["enabled"] for w in state["workspaces"]):
            return {
                "runtime": stopped,
                "configuration": state,
                "shutdown_confirmed": True,
                "policy": policy_summary(),
            }
        try:
            running = self.runtime.connect(state["generation"])
        except Exception:
            for item in state["workspaces"]:
                item["enabled"] = False
            state["generation"], state["updated_at"] = str(uuid4()), now()
            self.store.write(state)
            self.runtime.stop()
            raise
        return {"runtime": running, "configuration": state, "policy": policy_summary()}

    def check_not_state(self, root: Path) -> None:
        with (
            absolute_directory(root) as (root_fd, root_walk),
            absolute_directory(self.store.path) as (state_fd, state_walk),
        ):
            # Kernel identities also cover case, Unicode and volume-path aliases.
            require(
                identity(os.fstat(root_fd)) not in {identity(os.fstat(fd)) for fd in state_walk.fds}
                and identity(os.fstat(state_fd))
                not in {identity(os.fstat(fd)) for fd in root_walk.fds},
                "protected_root",
                "The private state directory cannot be shared or contain a shared directory.",
            )

    def verify_root(self, grant: dict) -> None:
        """A saved grant may only be activated through its canonical, unchanged root."""
        path = Path(grant["path"])
        check_root(path)
        self.check_not_state(path)
        with absolute_directory(path) as (fd, walk):
            actual = canonical_path(fd)
            require(
                actual is None or actual == path,
                "path_not_canonical",
                f"{path} is now spelled {actual} by the system. Run `keyhole forget` and open it again.",
            )
            require(
                identity(os.fstat(fd)) == (grant["device"], grant["inode"]),
                "root_changed",
                "The configured root was replaced. Close and forget the old grant before "
                "explicitly authorizing the replacement.",
            )
        SafeFS(grant).available()

    def open(
        self,
        path: Path,
        name: str | None = None,
        exclusions: list[str] | None = None,
        access: str | None = None,
        recovery: str | None = None,
    ) -> dict:
        require(access in (None, "ro", "rw"), "invalid_access", "Use ro or rw.")
        require(recovery in (None, "on", "off"), "invalid_recovery", "Use on or off.")
        path = path.expanduser()
        rules = None
        if exclusions is not None:
            rules = list(dict.fromkeys(normalize_pattern(x) for x in exclusions))
        explicit_name = name
        with self.store.lock():
            state = self.current()
            with absolute_directory(path) as (fd, walk):
                st = os.fstat(fd)
                root = canonical_path(fd) or path
            check_root(root)
            self.check_not_state(root)
            name = name or root.name
            for item in state["workspaces"]:
                if Path(item["path"]) != root:
                    continue
                require(
                    explicit_name is None or item["name"] == name,
                    "alias_conflict",
                    "This directory is already configured under a different alias; use that alias.",
                )
                require(
                    (item["device"], item["inode"]) == identity(st),
                    "root_changed",
                    "The configured root was replaced. Close and forget the old grant before "
                    "explicitly authorizing the replacement.",
                )
                require(
                    rules is None or item.get("exclusions", []) == rules,
                    "policy_conflict",
                    "Existing exclusion rules differ; close and forget the grant before explicitly "
                    "changing policy.",
                )
                mode_changed = (access is not None and access != item.get("access", "ro")) or (
                    recovery is not None and recovery != item.get("recovery", "on")
                )
                reducing_access = access == "ro" and item.get("access", "ro") == "rw"
                if access is not None:
                    item["access"] = access
                if recovery is not None:
                    item["recovery"] = recovery
                if item["enabled"] and not mode_changed:
                    status = self.runtime.status()
                    if status.get("process_running") and status.get("ready"):
                        return {
                            "changed": False,
                            "runtime": status,
                            "configuration": state,
                            "policy": policy_summary(),
                        }
                item["enabled"] = True
                if not reducing_access:
                    self.runtime.preflight()
                return self.transition(state)
            require(
                len(state["workspaces"]) < 64,
                "workspace_limit",
                "At most 64 workspaces can be configured.",
            )
            for item in state["workspaces"]:
                other = Path(item["path"])
                require(
                    name.casefold() != item["name"].casefold(),
                    "alias_conflict",
                    "Another workspace has that directory name. Supply an explicit unique --name alias.",
                )
                require(
                    other not in root.parents
                    and root not in other.parents
                    and identity(st) != (item["device"], item["inode"]),
                    "overlapping_roots",
                    "Overlapping or duplicate physical roots cannot be granted independently.",
                )
            state["workspaces"].append(
                {
                    "name": name,
                    "path": str(root),
                    "device": st.st_dev,
                    "inode": st.st_ino,
                    "enabled": True,
                    "access": access or "ro",
                    "recovery": recovery or "on",
                    "created_at": now(),
                    "exclusions": rules or [],
                }
            )
            self.runtime.preflight()
            return self.transition(state)

    def access(self, name: str, access: str) -> dict:
        require(access in ("ro", "rw"), "invalid_access", "Use ro or rw.")
        with self.store.lock():
            state = self.current()
            matches = [w for w in state["workspaces"] if w["name"] == name]
            require(len(matches) == 1, "workspace_unknown", "The alias is not configured.")
            grant = matches[0]
            if access == "rw":
                self.verify_root(grant)
            grant["access"] = access
            # Changing a saved permission does not reopen a closed workspace.
            # Revocation must reach transition's durable write even if a restart cannot pass preflight.
            if access == "rw" and any(w["enabled"] for w in state["workspaces"]):
                self.runtime.preflight()
            return self.transition(state)

    def select(
        self, names: list[str], all_workspaces: bool, *, enable: bool, forget: bool = False
    ) -> dict:
        require(
            bool(names) != all_workspaces, "invalid_selection", "Select workspace names or --all."
        )
        with self.store.lock():
            state = self.current()
            known = {w["name"] for w in state["workspaces"]}
            require(
                all(name in known for name in names),
                "workspace_unknown",
                "At least one selected alias is not configured.",
            )
            selected = known if all_workspaces else set(names)
            if enable:
                require(
                    bool(selected),
                    "workspace_unknown",
                    "No workspace is configured. Explicitly open a directory first.",
                )
            for item in state["workspaces"]:
                if item["name"] in selected:
                    if enable:
                        self.verify_root(item)
                    item["enabled"] = enable
            if forget:
                state["workspaces"] = [
                    item for item in state["workspaces"] if item["name"] not in selected
                ]
            if enable:
                self.runtime.preflight()
            return self.transition(state)

    def status(self) -> dict:
        try:
            state = self.current()
        except KeyholeError as exc:
            if exc.code != "not_configured":
                raise
            return {
                "observed_at": now(),
                "configured": False,
                "state_dir": str(self.store.path),
                "checks": self.runtime.checks(),
                "next_step": self.store.command("setup"),
            }
        result = self.runtime.status()
        live = bool(result.get("ready") and result.get("process_running"))
        return {
            "observed_at": now(),
            "configured": True,
            "state_dir": str(self.store.path),
            "checks": self.runtime.checks(),
            "configuration": state,
            "runtime": result,
            "effective_open_workspaces": [w["name"] for w in state["workspaces"] if w["enabled"]]
            if live
            else [],
            "auto_resume_after_boot": False,
        }
