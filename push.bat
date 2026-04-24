@echo off
echo Abortando rebase em progresso...
git rebase --abort
echo Puxando mudancas do GitHub...
git pull origin master
echo Adicionando arquivos...
git add .
echo Digite a mensagem do commit:
set /p MSG=
git commit -m "%MSG%"
echo Enviando para GitHub...
git push origin master
echo Concluido!
pause
