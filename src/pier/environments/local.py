import os
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Literal, Sequence

from pier.environments.base import BaseEnvironment, ExecResult, EnvironmentPath
from pier.environments.capabilities import EnvironmentCapabilities

class LocalEnvironment(BaseEnvironment):
    """
    LocalEnvironment runs commands directly on the local machine (or container).
    Useful when running inside a pre-isolated sandboxed container (e.g. GKE Pod).
    """

    @staticmethod
    def type() -> str:
        return "local"

    @property
    def capabilities(self) -> EnvironmentCapabilities:
        return EnvironmentCapabilities(
            gpus=False,
            disable_internet=True,
            mounted=True,
            preinstall_agents=True,
            filtered_egress=True,
            windows=False,
        )

    def _validate_definition(self):
        pass

    async def start(self, force_build: bool) -> None:
        self.trial_paths.mkdir()
        
        # Symlink /logs to the trial directory on the container filesystem
        logs_symlink = Path("/logs")
        if logs_symlink.exists() or logs_symlink.is_symlink():
            try:
                if logs_symlink.is_symlink():
                    logs_symlink.unlink()
                else:
                    shutil.rmtree(logs_symlink)
            except Exception as e:
                self.logger.warning(f"Could not clean up stale /logs path: {e}")
        
        try:
            os.symlink(self.trial_paths.trial_dir, logs_symlink)
            self.logger.info(f"Created symlink from /logs to {self.trial_paths.trial_dir}")
        except Exception as e:
            self.logger.error(f"Failed to create symlink for /logs: {e}")
            # Fallback: create the directories directly inside container root
            os.makedirs("/logs/agent", exist_ok=True)
            os.makedirs("/logs/verifier", exist_ok=True)
            os.makedirs("/logs/artifacts", exist_ok=True)

    async def stop(self, delete: bool):
        pass

    async def upload_file(self, source_path: Path | str, target_path: str):
        src = Path(source_path)
        dst = Path(target_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)

    async def upload_dir(self, source_dir: Path | str, target_dir: str):
        src = Path(source_dir)
        dst = Path(target_dir)
        if src.resolve() != dst.resolve():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)

    async def download_file(self, source_path: str, target_path: Path | str):
        src = Path(source_path)
        dst = Path(target_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)

    async def download_dir(self, source_dir: str, target_dir: Path | str):
        src = Path(source_dir)
        dst = Path(target_dir)
        if src.resolve() != dst.resolve():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        merged_env = self._merge_env(env) or {}
        full_env = {**os.environ, **merged_env}

        exec_cwd = cwd or str(self.environment_dir)

        self.logger.debug(f"Executing local command: {command} in {exec_cwd}")
        
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=exec_cwd,
                env=full_env,
            )

            if timeout_sec:
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=timeout_sec
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    stdout, stderr = await proc.communicate()
                    return ExecResult(
                        stdout=stdout.decode(errors="ignore"),
                        stderr=stderr.decode(errors="ignore") + "\nCommand timed out.",
                        return_code=-1,
                    )
            else:
                stdout, stderr = await proc.communicate()

            return ExecResult(
                stdout=stdout.decode(errors="ignore"),
                stderr=stderr.decode(errors="ignore"),
                return_code=proc.returncode,
            )
        except Exception as e:
            self.logger.error(f"Failed to execute local command: {e}")
            return ExecResult(
                stdout="",
                stderr=str(e),
                return_code=-1,
            )
