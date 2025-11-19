#!/bin/bash
while IFS= read -r file; do
  # Skip LLM-CONTEXT files and empty lines
  if [[ "$file" =~ ^LLM-CONTEXT/ ]] || [[ -z "$file" ]]; then
    continue
  fi
  
  case "$file" in
    *test*.py|*_test.py|test_*.py|*/tests/*) echo "TEST: $file" ;;
    *.py) echo "CODE: $file" ;;
    *.md|*.rst|*.txt) echo "DOCS: $file" ;;
    *) echo "ARTIFACT: $file" ;;
  esac
done < <(git diff --name-only HEAD && git ls-files --others --exclude-standard) | sort
