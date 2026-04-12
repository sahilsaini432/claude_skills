### Install Skill from folder
``` bash
cp -r {skill_path}/ ~/.claude/skills/
```

### Install .skill (windows - Powershell)
``` bash
# Extract and install
Expand-Archive brain-wiki.skill -DestinationPath $HOME\.claude\skills\ -Force
```

### Install .skill (mac)
``` bash
unzip brain-wiki.skill -d ~/.claude/skills/
```

### After installing (.skill), create your .env:
```bash
powershellNew-Item -Path "$HOME\.claude\skills\brain-wiki\.env" -ItemType File
Add-Content "$HOME\.claude\skills\brain-wiki\.env" "BRAIN_VAULT_ROOT=path\to\brain"
Add-Content "$HOME\.claude\skills\brain-wiki\.env" "LOCAL_LLM_URL={url_to_llm}"
Add-Content "$HOME\.claude\skills\brain-wiki\.env" "LOCAL_LLM_MODEL={model_name}"
```

```bash
echo "BRAIN_VAULT_ROOT=/Users/you/brain" >> ~/.claude/skills/brain-wiki/.env
echo "LOCAL_LLM_URL={url_to_llm}" >> ~/.claude/skills/brain-wiki/.env
echo "LOCAL_LLM_MODEL={model_name}" >> ~/.claude/skills/brain-wiki/.env
```
