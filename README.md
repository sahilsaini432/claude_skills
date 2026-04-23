### Install Skill from folder
``` bash
cp -r {skill_path}/ ~/.claude/skills/
```

### Install .skill (windows - Powershell)
``` bash
# Extract and install
Expand-Archive cortex.skill -DestinationPath $HOME\.claude\skills\ -Force
```

### Install .skill (mac)
``` bash
unzip cortex.skill -d ~/.claude/skills/
```

### After installing (.skill), create your .env:
```bash
powershellNew-Item -Path "$HOME\.claude\skills\cortex\.env" -ItemType File
Add-Content "$HOME\.claude\skills\cortex\.env" "BRAIN_VAULT_ROOT=path\to\brain"
Add-Content "$HOME\.claude\skills\cortex\.env" "LOCAL_LLM_URL={url_to_llm}"
Add-Content "$HOME\.claude\skills\cortex\.env" "LOCAL_LLM_MODEL={model_name}"
```

```bash
echo "BRAIN_VAULT_ROOT=/Users/you/brain" >> ~/.claude/skills/cortex/.env
echo "LOCAL_LLM_URL={url_to_llm}" >> ~/.claude/skills/cortex/.env
echo "LOCAL_LLM_MODEL={model_name}" >> ~/.claude/skills/cortex/.env
```
