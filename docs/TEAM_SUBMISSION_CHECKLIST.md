# Team Submission Checklist

Work through this list as a team before turning in the project.

## Code and testing

- [ ] Run `run_tests_windows.bat` on at least one team member's computer. Record the result (passed, failed, skipped).
- [ ] Run `run_release_check_windows.bat` and keep `reports/release_verification.md`.
- [ ] Optional: run `verify_distributed.py` and keep its JSON report.
- [ ] If Docker is part of the submission: start Docker Desktop and run `verify_docker_windows.bat`. Otherwise, say that Docker was not tested.
- [ ] Make sure no real names, addresses, donors or clients were entered into any database you submit. The sample data is fictional.
- [ ] Do not submit `.venv/`, `data/*.db`, `data/presentation_sessions/` or `reports/` output unless the instructor asks for them.

## Documentation

- [ ] Read through `README.md` and `docs/FINAL_TECHNICAL_REPORT.md` together and agree on the wording.
- [ ] Fill in `docs/TEAM_CONTRIBUTIONS.md`. Each person lists only their own work, and another member confirms it.
- [ ] Check the background references (8-10) in the technical report, then format all references in the style your instructor wants.
- [ ] Check the course syllabus or ask the instructor whether a statement about tools or outside help used during development is required. If it is, add it in the format the course asks for.
- [ ] Add the course number, section and instructor name if required.
- [ ] Convert the report to the required format (for example PDF), if needed.

## Presentation

- [ ] Assign speakers in `docs/PRESENTATION.md` and in section 4 of `docs/TEAM_CONTRIBUTIONS.md`.
- [ ] Make the slides using the PNG diagrams in `docs/diagrams/` and real screenshots from your own run.
- [ ] Practice the five-minute demo at least once with `run_presentation_windows.bat`, then restart it for fresh data.
- [ ] Prepare the backup plan (diagrams plus `python presentation_story.py`) in case the live demo fails.

## Repository

- [ ] Agree as a team on who uploads the final version to the team repository (https://github.com/kaitlinalbey/food-bank) and when.
- [ ] Compare this version with the team repository before replacing anything, so no teammate's newer work is lost.
- [ ] The frontend teammate's final design changes should be merged and the tests run again before the final upload.
