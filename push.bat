@echo off
echo Adicionando arquivos...
git add .

echo Commitando mudancas...
git commit -m "Update: Password recovery system with PostgreSQL database integration"

echo Puxando mudancas do GitHub...
git pull --rebase

echo Enviando para GitHub...
git push

echo Concluido!
pause
