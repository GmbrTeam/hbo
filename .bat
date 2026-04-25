@echo off
echo Abortando rebase em progresso...
git rebase --abort
echo Puxando mudancas do GitHub...
git pull origin master
echo.
echo Escolha uma opcao:
echo 1 - Adicionar todos os arquivos
echo 2 - Selecionar arquivos especificos
set /p CHOICE=

if "%CHOICE%"=="1" goto ALL
if "%CHOICE%"=="2" goto SELECT
echo Opcao invalida.
pause
exit /b

:ALL
echo Adicionando todos os arquivos...
git add .
goto COMMIT

:SELECT
echo Listando arquivos...
setlocal enabledelayedexpansion
set count=0
for /f "delims=" %%f in ('git ls-files') do (
    set /a count+=1
    set "file!count!=%%f"
    echo !count! - %%f
)
if !count!==0 (
    echo Nenhum arquivo para adicionar.
    pause
    exit /b
)
echo.
echo Digite os numeros dos arquivos que deseja adicionar (ex: 12 para arquivos 1 e 2):
set /p NUMBERS=

set i=1
:LOOP
if !i! gtr !count! goto DONELOOP
set "NUM=!NUMBERS!"
set "NUM=!NUM:%i%=!"
if not "!NUM!"=="%NUMBERS%" (
    git add -f "!file%i%!"
    echo Adicionado: !file%i%!
)
set /a i+=1
goto LOOP

:DONELOOP
endlocal
goto COMMIT

:COMMIT
echo Verificando se ha mudancas para commitar...
for /f %%i in ('git diff --cached --name-only') do set HAS_CHANGES=1
if not defined HAS_CHANGES (
    echo Nenhuma mudanca para commitar. Nada sera enviado.
    pause
    exit /b
)
echo Digite a mensagem do commit:
set /p MSG=
git commit -m "%MSG%"
echo Enviando para GitHub...
git push origin master
echo Concluido!
pause