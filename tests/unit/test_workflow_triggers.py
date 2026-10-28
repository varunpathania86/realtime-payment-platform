import unittest

import yaml

from scripts.module_catalog import ROOT


class WorkflowTriggerTests(unittest.TestCase):
    def test_only_push_and_fork_pr_events_start_check_jobs(self):
        guard = (
            "github.event_name != 'pull_request' || "
            "github.event.pull_request.head.repo.full_name != github.repository"
        )
        for filename, roots in (("build.yml", {"catalog"}), ("security.yml", {"plan", "solution"})):
            with self.subTest(workflow=filename):
                workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
                # PyYAML's YAML 1.1 loader interprets the Actions key "on" as True.
                triggers = workflow[True]
                self.assertIn("push", triggers)
                self.assertIn("pull_request", triggers)
                self.assertEqual(triggers["push"]["branches-ignore"], ["automation/**"])
                jobs = workflow["jobs"]
                self.assertEqual({name for name, job in jobs.items() if not job.get("needs")}, roots)
                for name in roots:
                    self.assertEqual(jobs[name]["if"], guard)
                for name, job in jobs.items():
                    if name in roots:
                        continue
                    dependencies = job["needs"]
                    if isinstance(dependencies, str):
                        dependencies = [dependencies]
                    self.assertTrue(set(dependencies) & roots)
                    self.assertNotIn("always()", job.get("if", ""))

    def test_publication_still_requires_successful_same_repository_push(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())
        condition = workflow["jobs"]["plan"]["if"]
        self.assertIn("github.event.workflow_run.event == 'push'", condition)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", condition)
        self.assertIn("github.event.workflow_run.head_repository.full_name == github.repository", condition)
