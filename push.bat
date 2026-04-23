@echo off
echo Adicionando arquivos...
git add .

echo Commitando mudancas...
git commit -m "Update: Password recovery system with PostgreSQL database integration"

echo Enviando para GitHub...
git push

echo Concluido!
pause
