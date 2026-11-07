@echo off
REM Deletes files/folders in Playwriteagent that nothing in run_agent.py / qa / qa_ui actually
REM uses anymore - verified by grepping the real code, not guessed. Run this FROM INSIDE the
REM Playwriteagent folder (cd there first), and read the list below before running it.
REM
REM NOT touched (still actively used): run_agent.py, qa\, qa_ui\, conftest.py, pytest.ini,
REM requirements.txt, .env, .env.example, .auth\, inventory\, scenarios\, generated_tests\,
REM test_library\, reports\

echo About to delete old/unused files and folders from this project.
echo Press Ctrl+C now to cancel, or
pause

REM --- old prototype entry points, superseded by run_agent.py ---
del /q agent_pom.py 2>nul
del /q agent.py 2>nul
del /q agent.bat 2>nul
del /q code.bat 2>nul
del /q live.bat 2>nul
del /q run.bat 2>nul
del /q heal.bat 2>nul
del /q medcross.bat 2>nul
del /q heal_tests.py 2>nul
del /q langchain_agent.py 2>nul
del /q medcross_agent.py 2>nul
del /q medcross_code_agent.py 2>nul
del /q medcross_live.py 2>nul
del /q pom_generator.py 2>nul
del /q export_to_excel.py 2>nul
del /q conftest_old.py 2>nul

REM --- old planning/prompt notes, superseded by qa\scenarios.py's embedded prompt ---
del /q system_prompt.txt 2>nul
del /q prompt_brainstorm.txt 2>nul
del /q prompt_plan.txt 2>nul

REM --- stray old reports/repository files, superseded by test_library\ ---
del /q all_test_cases_repository.csv 2>nul
del /q test_execution_report.csv 2>nul
del /q report.html 2>nul

REM --- old test fixtures, unreferenced ---
del /q dummy_image.jpg 2>nul
del /q dummy0.txt dummy1.txt dummy2.txt dummy3.txt dummy4.txt dummy5.txt 2>nul

REM --- old memory experiment, unreferenced ---
del /q memory_store.py 2>nul
rmdir /s /q memory 2>nul

REM --- old folders superseded by the current pipeline ---
rmdir /s /q pages 2>nul
rmdir /s /q test_cases 2>nul
rmdir /s /q assets 2>nul

REM --- regenerable caches ---
rmdir /s /q __pycache__ 2>nul
rmdir /s /q .pytest_cache 2>nul

echo.
echo Done. Left in place (still used): run_agent.py, qa, qa_ui, conftest.py, pytest.ini,
echo requirements.txt, .env, .auth, inventory, scenarios, generated_tests, test_library, reports.
echo.
echo venv\ was NOT deleted automatically - it's large and not referenced by any script, so it looks
echo unused, but double check nothing of yours depends on it before removing it yourself.
pause
