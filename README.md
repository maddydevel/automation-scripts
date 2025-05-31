# automation-scripts

Here are two effective methods to automate Docker installation on Ubuntu, based on the repository you referenced ([maddydevel/automation-scripts](https://github.com/maddydevel/automation-scripts/tree/main)):

### Method 1: Using the Python Script (Recommended)
1. **Download the script**:
   ```bash
   wget https://raw.githubusercontent.com/maddydevel/automation-scripts/main/install_docker_ubuntu.py
   ```

2. **Run with Python 3**:
   ```bash
   sudo python3 install_docker_ubuntu.py
   ```
   - Automatically handles:
     - Package updates
     - Docker repository setup
     - Docker Engine installation
     - User permissions (adds your user to `docker` group if run with `sudo` and `SUDO_USER` is available)

### Method 2: Bash Script Alternative
1. **Download and execute**:
   ```bash
   curl -sSL https://raw.githubusercontent.com/maddydevel/automation-scripts/main/install_docker_ubuntu.sh | sudo bash
   ```
   - Features:
     - Non-interactive mode (auto-accepts prompts)
     - Post-install verification (`docker --version` check)
     - Attempts to add `$SUDO_USER` to the `docker` group.

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

> **Note**: Both scripts are primarily designed and tested for Ubuntu 24.04 LTS ("Noble Numbat") but aim to be adaptable. For other versions, you might need to review OS codename settings within the scripts. The Python version generally provides more detailed error handling and logging.

## Recent Improvements

Both the Python (`install_docker_ubuntu.py`) and Bash (`install_docker_ubuntu.sh`) scripts have been significantly updated to enhance their robustness, security, and usability.

**Python Script (`install_docker_ubuntu.py`):**
*   **Enhanced Security:** Refactored GPG key import and APT repository setup to eliminate the use of `shell=True`, preferring direct command execution and file manipulation where possible.
*   **Improved Command Handling:** The internal `run_command` utility has been improved for safer subprocess management and now supports chaining command I/O without resorting to the shell.
*   **Idempotency:** The script is now more idempotent, particularly in its repository and GPG key configuration steps. It checks for existing configurations before applying changes.
*   **Standardized Logging:** Logging has been standardized and made more detailed throughout the script, offering better insight into its operations and making debugging easier.

**Bash Script (`install_docker_ubuntu.sh`):**
*   **Improved Old Package Removal:** The process for removing old Docker versions is now more thorough, including purging configuration files and auto-removing dependencies.
*   **Idempotency:** APT repository setup now checks if the configuration already exists and is correct, avoiding redundant operations.
*   **Clearer Logging:** Logging has been made more consistent and descriptive.

These updates aim to provide a more reliable, secure, and user-friendly experience for automating Docker installation on Ubuntu.
