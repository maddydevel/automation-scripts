# automation-scripts

Here are two effective methods to automate Docker installation on Ubuntu, based on the repository you referenced ([maddydevel/automation-scripts](https://github.com/maddydevel/automation-scripts/tree/main)):

### Method 1: Using the Python Script (Recommended)
1. **Download the script**:
   ```bash
   wget https://raw.githubusercontent.com/maddydevel/automation-scripts/main/docker_installer.py
   ```

2. **Run with Python 3**:
   ```bash
   python3 docker_installer.py
   ```
   - Automatically handles:
     - Package updates
     - Docker repository setup
     - Docker Engine installation
     - User permissions (adds your user to `docker` group)

### Method 2: Bash Script Alternative
1. **Download and execute**:
   ```bash
   curl -sSL https://raw.githubusercontent.com/maddydevel/automation-scripts/main/docker_install.sh | bash
   ```
   - Features:
     - Non-interactive mode (auto-accepts prompts)
     - Post-install verification (`docker --version` check)

### Key Automation Steps (Both Methods)
- Updates apt packages (`apt-get update`)
- Installs prerequisite packages (`ca-certificates`, `curl`, etc.)
- Adds Docker's official GPG key and repository
- Installs Docker Engine, CLI, Containerd
- Enables and starts Docker service
- Configures user permissions

### Post-Install Verification
After automation completes, verify with:
```bash
docker run hello-world
```

> **Note**: Both scripts assume Ubuntu 20.04/22.04 LTS. For other versions, you may need to modify the repository links in the scripts. The Python version provides better error handling and logging.
