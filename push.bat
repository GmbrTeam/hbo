@echo off
git rebase --abort
git add .
echo Type commit message:
set /p MSG=
git commit -m "%MSG%"
git push origin master
pause
