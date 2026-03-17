# Git + GitHub - Шпаргалка (Windows 11)

---

## Установка и настройка

```bash
# Скачать: https://git-scm.com/download/win
# После установки - первичная настройка:

git config --global user.name "Твоё Имя"
git config --global user.email "email@example.com"
git config --global core.editor "code --wait"   # VS Code как редактор
git config --global init.defaultBranch main      # ветка по умолчанию

git config --list                                # посмотреть все настройки
```

---

## Создание репозитория

```bash
git init                        # инициализировать новый репо в текущей папке
git init my-project             # создать папку и инициализировать в ней

git clone <url>                 # клонировать удалённый репо
git clone <url> my-folder       # клонировать в конкретную папку
git clone --depth 1 <url>       # shallow clone (только последний коммит)
```

---

## Основной рабочий цикл

```bash
git status                      # состояние рабочей директории
git status -s                   # короткий вывод (M = modified, ?? = untracked)

git add <file>                  # добавить файл в staging area
git add .                       # добавить всё
git add -p                      # интерактивно добавлять куски изменений (patch)

git commit -m "сообщение"       # зафиксировать изменения
git commit -am "сообщение"      # add + commit для уже отслеживаемых файлов
git commit --amend              # изменить последний коммит (сообщение или файлы)
git commit --amend --no-edit    # поправить последний коммит без смены сообщения
```

---

## Просмотр истории

```bash
git log                         # полный лог
git log --oneline               # компактный лог
git log --oneline --graph       # лог с деревом веток
git log --oneline --all         # включая удалённые ветки
git log -5                      # последние 5 коммитов
git log --author="Серёжа"       # фильтр по автору
git log -- <file>               # история конкретного файла

git show <commit>               # подробности коммита
git diff                        # изменения в рабочей директории (не staged)
git diff --staged               # изменения в staging area
git diff <branch1>..<branch2>   # разница между ветками
```

---

## Ветки

```bash
git branch                      # список локальных веток
git branch -a                   # все ветки (включая удалённые)
git branch -v                   # ветки с последним коммитом

git branch <name>               # создать ветку
git checkout <name>             # переключиться на ветку
git checkout -b <name>          # создать и сразу переключиться
git switch <name>               # современный аналог checkout
git switch -c <name>            # создать и переключиться (современный)

git branch -d <name>            # удалить ветку (если слита)
git branch -D <name>            # удалить ветку принудительно

git branch -m <old> <new>       # переименовать ветку
git branch -m <new>             # переименовать текущую ветку
```

---

## Слияние и перебазирование

```bash
# Merge
git merge <branch>              # слить ветку в текущую
git merge --no-ff <branch>      # слить с merge-коммитом (без fast-forward)
git merge --squash <branch>     # слить, сжав все коммиты в один (без коммита)
git merge --abort               # отменить слияние при конфликте

# Rebase
git rebase <branch>             # перебазировать текущую ветку на другую
git rebase -i HEAD~3            # интерактивный rebase последних 3 коммитов
git rebase --abort              # отменить rebase
git rebase --continue           # продолжить после разрешения конфликта

# Cherry-pick
git cherry-pick <commit>        # перенести конкретный коммит в текущую ветку
git cherry-pick <c1>..<c2>      # перенести диапазон коммитов
```

---

## Работа с удалённым репозиторием (GitHub)

```bash
# Remote
git remote -v                           # список удалённых репо
git remote add origin <url>             # привязать удалённый репо
git remote set-url origin <new-url>     # сменить URL
git remote remove origin                # удалить привязку

# Push / Pull / Fetch
git fetch origin                        # скачать изменения без слияния
git fetch --all                         # скачать со всех remote

git pull                                # fetch + merge
git pull --rebase                       # fetch + rebase (чище история)
git pull origin <branch>                # pull конкретной ветки

git push origin <branch>                # запушить ветку
git push -u origin <branch>             # запушить и установить upstream
git push                                # пушить в upstream-ветку
git push --force-with-lease             # force push (безопасный, проверяет remote)
git push origin --delete <branch>       # удалить ветку на remote
git push --tags                         # запушить все теги
```

---

## Теги

```bash
git tag                         # список тегов
git tag v1.0.0                  # лёгкий тег
git tag -a v1.0.0 -m "Release"  # аннотированный тег
git tag -a v1.0.0 <commit>      # тег на конкретный коммит

git push origin v1.0.0          # запушить один тег
git push origin --tags          # запушить все теги

git tag -d v1.0.0               # удалить тег локально
git push origin --delete v1.0.0 # удалить тег на remote
```

---

## Откат и исправление ошибок

```bash
# Отменить изменения в файле (до staging)
git restore <file>              # сбросить изменения в рабочей директории
git restore --staged <file>     # убрать файл из staging (unstage)

# Отменить коммиты
git revert <commit>             # создать новый коммит, отменяющий указанный
git revert HEAD                 # отменить последний коммит (безопасно)

# Reset (осторожно!)
git reset --soft HEAD~1         # отменить коммит, оставить изменения staged
git reset --mixed HEAD~1        # отменить коммит, оставить изменения unstaged (по умолчанию)
git reset --hard HEAD~1         # отменить коммит и выбросить изменения совсем

# Найти потерянный коммит
git reflog                      # история всех операций HEAD
git checkout <commit>           # переключиться на любой коммит (detached HEAD)
```

---

## Stash - временное сохранение

```bash
git stash                       # сохранить незакоммиченные изменения
git stash push -m "описание"    # сохранить с именем
git stash list                  # список stash'ей
git stash pop                   # применить последний stash и удалить его
git stash apply stash@{2}       # применить конкретный stash (без удаления)
git stash drop stash@{0}        # удалить stash
git stash clear                 # удалить все stash'и
git stash branch <name>         # создать ветку из stash
```

---

## .gitignore

```bash
# Создать файл .gitignore в корне репо
# Примеры паттернов:

*.log           # все файлы с расширением .log
/node_modules   # папка node_modules в корне
build/          # любая папка build
!important.log  # исключение - этот файл НЕ игнорировать

# Применить .gitignore к уже отслеживаемым файлам:
git rm -r --cached .
git add .
git commit -m "apply .gitignore"

# Глобальный .gitignore (для всех проектов):
git config --global core.excludesFile "%USERPROFILE%\.gitignore_global"
```

---

## GitHub - специфика

```bash
# Аутентификация через GitHub CLI (рекомендуется на Windows):
# Скачать: https://cli.github.com
gh auth login

# Или через Personal Access Token (PAT):
# GitHub -> Settings -> Developer settings -> Personal access tokens
# При git push Windows попросит логин - вводишь токен вместо пароля

# SSH-ключи (альтернатива):
ssh-keygen -t ed25519 -C "email@example.com"   # генерация ключа
# Добавить публичный ключ (~/.ssh/id_ed25519.pub) в GitHub -> Settings -> SSH keys
ssh -T git@github.com                           # проверить подключение
```

### GitHub Flow (стандартный рабочий процесс)

```
main
 └── feature/my-feature    <- создаёшь ветку
      └── коммиты          <- работаешь
      └── Push + Pull Request на GitHub
      └── Code Review
      └── Merge в main
      └── Удалить ветку
```

```bash
# Типичный сценарий:
git switch -c feature/cool-thing
# ... работаешь ...
git add .
git commit -m "feat: add cool thing"
git push -u origin feature/cool-thing
# Идёшь на GitHub -> создаёшь Pull Request
```

---

## Полезные алиасы (в Git Bash / PowerShell)

```bash
git config --global alias.st status
git config --global alias.co checkout
git config --global alias.br branch
git config --global alias.lg "log --oneline --graph --all"
git config --global alias.undo "reset --soft HEAD~1"
```

---

## Частые проблемы на Windows

| Проблема | Решение |
|----------|---------|
| Конфликт line endings (CRLF/LF) | `git config --global core.autocrlf true` |
| Кириллица в именах файлов кракозябры | `git config --global core.quotepath false` |
| SSL certificate error | `git config --global http.sslBackend schannel` |
| Credential prompt каждый раз | `git config --global credential.helper manager` |

---

## Шпаргалка по состояниям файла

```
Untracked -> git add -> Staged -> git commit -> Committed
                                                    |
                          git restore --staged <-   |
              git restore <-  (unstage)         (unmodify)
```
