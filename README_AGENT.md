# QA Agent – how to use it

## One-time setup
1. Add these lines to your `.env` (see `.env.example`):
   ```
   APP_USERNAME=your-login
   APP_PASSWORD=your-password
   APP_SOURCE_DIR=C:\medcrossproject
   ```
2. Start the Medcross app and make sure http://localhost:2786 opens in your browser.

## Run
Double-click **agent.bat** (or type `agent.bat` in the folder). The agent asks you:

1. **WHERE**: the page, e.g. `Contacts/PracticeReferralDoctor`
2. If the page has saved test sets: **N** for new test cases, or a number to re-run a saved set
3. **WHAT**: what to test in your own words, e.g. `mobile number validation`, `saving with missing required fields` (Enter = whole page)
4. How many test cases, whether tests may save data, and whether to show the browser

It then **shows you the test cases** (steps and expected result). Press Enter to run them all, type numbers to skip some (`2,5`), or `q` to cancel.
When the run finishes, the report opens in your browser.

Without questions (for scheduling):
```
agent.bat Contacts/PracticeReferralDoctor --focus "mobile number validation" --no-writes
agent.bat Contacts/PracticeReferralDoctor --rerun          (re-run the latest saved set)
```
Options: `--count N`, `--review` (show the test cases before running), `--headed`, `--heal 0`, `--no-open`

## Semi-automatic mode (review in Excel before anything runs)
Instead of reviewing in the terminal, you can review in Excel:
```
agent.bat Contacts/PracticeReferralDoctor --focus "mobile number validation" --draft
```
This writes `draft_review.xlsx` and stops - nothing runs yet. Open it (it opens automatically on
Windows) and fill the **Decision** column for every row:
- **Approve** - run it as written
- **Edit** - write what to change in the **Comment** column (e.g. "also check the missing-name error");
  the agent asks GPT to make just that change, then re-checks it against the real page before running it
- **Reject**, or leave it **blank** - never runs, never gets saved as a real test case

Save the file, then just double-click **agent.bat** again (no need to type the `--apply` path) -
it automatically notices you have a filled-in review waiting and asks:
```
You have a filled-in review waiting for Contacts/PracticeReferralDoctor - run it now? (Y/n)
```
Say yes and it runs it straight away. (If you'd rather type the path yourself, that still works too:
`agent.bat --apply "test_library\...\draft_review.xlsx"`.)

Only what you approved or successfully edited is saved as a test case and run; rejected and
undecided rows are kept in the Excel file for the record but never executed. If a requested edit
can't be made safely, the original test case is kept and the report says so.

Notes on the automatic check:
- It only offers a draft once you've actually typed something in the Decision column - an
  untouched draft is left alone (no nagging).
- It checks every time you start `agent.bat` in interactive mode, before asking WHERE.
- Once it's been run, it stops being "pending" and won't be offered again.

In the interactive menu (`agent.bat` with no page), you're asked "review here or in Excel?" after
you describe what to test.

## Where everything is saved
```
test_library\
  test_case_repository.csv                       every test case ever generated, with its last result (Excel)
  contacts_practicereferraldoctor\
    20261023_121500_mobile_number_validation\     one folder per run
      test_cases.csv     readable test cases: steps, expected result, result, reason
      scenarios.json     the same test cases for re-running
      test_....py        the Playwright test code
      report.html        the report (also copied to reports\report.html)
      screenshots\       screenshots of failures
```
Test IDs never repeat (TC_PRD_013 follows TC_PRD_012 from an earlier run).

## What happens
| Step | What | Output |
|---|---|---|
| 1 Preflight | server up? login works? → saves the session once | `.auth\state.json` |
| 2 Inventory | scans the live page + reads `.aspx` / `.aspx.cs`; every selector is checked to match exactly 1 element | `inventory\<page>.json` |
| 3 Scenarios | GPT designs tests **using only inventory keys**; each one is validated (fields exist, options exist, validators can really fire with that value, messages exist in code). Invalid ones are sent back once for fixing. You review them before they run | `test_library\...\test_cases.csv` |
| 4 Render | Python tests are produced from a template (not written by GPT) → always valid code | `test_library\...\test_*.py` |
| 5 Run + heal | only `SCRIPT_ERROR` tests are healed: re-scan, then GPT may change steps but **never the expected results** | |
| 6 Report | status, category, reason, failed step, screenshot | `reports\report.html`, `report.csv` |

## Reading the report
- **App defect**: the app did not do what its own validators/code say. Look at the app.
- **Script error**: the test could not do a step (element hidden or missing). Healing failed. Look at the scenario.
- **Environment**: app down, login failed, or browser problem. Fix that and re-run.
- **Smoke**: tests whose expected result couldn't be proven from the code only check that nothing crashes.

## Notes
- To change a saved test, edit `scenarios.json` in its run folder, then re-run that set from the menu.
- `.auth\state.json` contains your login session cookie, so don't share or commit it.
- Your old scripts (`medcross_agent.py`, `agent_pom.py`, `heal_tests.py`, …) are no longer needed. The old `conftest.py` is kept as `conftest_old.py`.
