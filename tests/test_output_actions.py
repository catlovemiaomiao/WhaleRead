from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from native_launcher import TranslatorController


class OutputActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def controller(self, root: Path) -> TranslatorController:
        owner = TranslatorController(
            workspace=root / "jobs",
            settings=QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat),
            restore=False,
        )
        self.addCleanup(owner.shutdown)
        return owner

    @staticmethod
    def completed_task(root: Path) -> tuple[Path, Path]:
        source = root / "原文" / "sample.txt"
        source.parent.mkdir(parents=True)
        source.write_text("Synthetic text.", encoding="utf-8")
        output = root / "译文" / "sample.zh-CN.txt"
        output.parent.mkdir(parents=True)
        output.write_text("自建测试译文。", encoding="utf-8")
        epub = output.with_suffix(".epub")
        epub.write_bytes(b"synthetic epub fixture")
        config = {
            "source": {"files": ["原文/sample.txt"]},
            "output": {"directory": "译文", "filename_rule": "原文件名.zh-CN.txt"},
        }
        (root / "翻译任务.yaml").write_text(
            yaml.safe_dump(config, allow_unicode=True), encoding="utf-8"
        )
        return output, epub

    def test_reveal_recovers_output_after_summary_only_startup(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = root / "jobs" / "sample"
            output, epub = self.completed_task(job)
            owner = self.controller(root)
            owner._job_path = str(job)
            self.assertEqual("", owner.outputPath)

            with patch("native_launcher.reveal_file") as run:
                owner.revealOutput()

            self.assertEqual(str(output), owner.outputPath)
            run.assert_called_once_with(epub)

    def test_open_recovers_and_opens_the_existing_epub(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = root / "jobs" / "sample"
            output, epub = self.completed_task(job)
            owner = self.controller(root)
            owner._job_path = str(job)

            with patch("native_launcher.QDesktopServices.openUrl") as open_url:
                owner.openOutput()

            self.assertEqual(str(output), owner.outputPath)
            opened = open_url.call_args.args[0]
            self.assertEqual(str(epub), opened.toLocalFile())

    def test_reveal_falls_back_to_job_without_treating_empty_path_as_cwd(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = root / "jobs" / "sample"
            job.mkdir(parents=True)
            owner = self.controller(root)
            owner._job_path = str(job)

            with patch("native_launcher.reveal_file") as run:
                owner.revealOutput()

            run.assert_called_once_with(job)

    def test_reveal_with_no_paths_does_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            owner = self.controller(Path(raw).resolve())
            with patch("native_launcher.reveal_file") as run, \
                    patch("native_launcher.QDesktopServices.openUrl") as open_url:
                owner.revealOutput()
                owner.openOutput()
            run.assert_not_called()
            open_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()
