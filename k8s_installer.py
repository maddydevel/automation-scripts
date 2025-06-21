import os
import subprocess
import sys
import time
import getpass

"""
Kubernetes Cluster Installation Script for Ubuntu 24.04

This script automates the setup of a single-master Kubernetes cluster using kubeadm
on pre-existing Ubuntu 24.04 servers.

Features:
- Asks for the number of master (1 for this script) and worker nodes.
- Gathers server IP addresses, SSH username, and initial SSH passwords.
- Generates a dedicated SSH key pair for passwordless authentication to servers.
- Copies the public key to all specified servers.
- Installs necessary prerequisites:
    - Disables swap.
    - Configures required kernel modules (overlay, br_netfilter).
    - Sets sysctl parameters for Kubernetes networking.
    - Installs base packages (curl, apt-transport-https, etc.).
- Installs container runtime (containerd) and configures it with SystemdCgroup.
- Installs Kubernetes components: kubeadm, kubelet, kubectl.
- Initializes the master node using 'kubeadm init'.
- Sets up kubeconfig for the SSH user on the master node.
- Installs a CNI plugin (Calico by default) on the master node.
- Joins worker nodes to the cluster using the token from the master.
- Provides instructions for accessing the cluster post-installation.

Prerequisites on the machine running this script:
- Python 3.6+
- Paramiko library (`pip install paramiko`)
- `sshpass` utility (recommended for smoother initial SSH key copy):
  `sudo apt-get update && sudo apt-get install sshpass`
- Internet access to download packages and container images.

Prerequisites on the target Ubuntu 24.04 servers:
- Ubuntu 24.04 LTS installed.
- SSH server enabled and accessible.
- A user account with sudo privileges (password required for initial SSH key setup).
- Internet access from each server to download packages and container images.
- Servers should be on the same network and able to reach each other via IP.
- Minimum 2 CPUs and 2GB RAM per node (master and worker) is recommended.

Usage:
1. Ensure all prerequisites are met.
2. Make the script executable: `chmod +x k8s_installer.py`
3. Run the script: `./k8s_installer.py`
4. Follow the on-screen prompts to provide server information.

Security Note:
- The script requires initial SSH passwords to set up passwordless authentication.
  These passwords are used by `sshpass` or Paramiko for the `ssh-copy-id` equivalent operation.
- The generated SSH private key (`id_rsa_k8s_auto`) is stored in `~/.ssh/` on the machine
  running the script. Protect this key.
- Consider network security (firewalls) for your cluster nodes. This script does not
  configure firewalls beyond what's necessary for Kubernetes components to communicate
  (e.g., it doesn't explicitly open ports on a host firewall like ufw). You might
  need to adjust firewall rules based on your CNI plugin and application needs.
  Refer to Kubernetes and CNI documentation for port requirements.
"""
import os
import subprocess
import sys
import time
import getpass
import socket # For socket.timeout

# Try to import Paramiko, prompt for installation if not found
try:
    import paramiko
except ImportError:
    print("[CRITICAL ERROR] Paramiko library is not installed. Please install it by running:")
    print("pip3 install paramiko") # recommend pip3
    sys.exit(1)

# --- Configuration ---
SSH_KEY_NAME = "id_rsa_k8s_auto" # Name for the auto-generated SSH key
SSH_KEY_PATH = os.path.expanduser(f"~/.ssh/{SSH_KEY_NAME}")
SSH_PUBLIC_KEY_PATH = f"{SSH_KEY_PATH}.pub"
DEFAULT_POD_NETWORK_CIDR = "10.244.0.0/16"  # Default for Flannel, common for others
CALICO_MANIFEST_URL = "https://docs.projectcalico.org/manifests/calico.yaml"

# --- Helper Functions ---
def print_info(message):
    print(f"[INFO] {message}")

def print_warning(message):
    print(f"[WARN] {message}")

def print_error(message):
    print(f"[ERROR] {message}")
    # sys.exit(1) # Allow potential cleanup or further error reporting

def print_critical_error(message):
    """Prints a critical error and exits immediately."""
    print(f"[CRITICAL ERROR] {message}")
    sys.exit(1)

def run_local_command(command, check=True, sensitive_output=False):
    """Runs a command locally."""
    display_command = command
    if sensitive_output: # crude way to hide passwords for e.g. sshpass
        parts = command.split()
        if '-p' in parts:
            try:
                idx = parts.index('-p') + 1
                if idx < len(parts):
                    parts[idx] = "'********'"
                    display_command = " ".join(parts)
            except ValueError:
                pass # Should not happen if -p is present

    print_info(f"Running local command: {display_command}")
    try:
        process = subprocess.run(command, shell=True, check=check, capture_output=True, text=True)
        if process.stdout:
            print_info(f"Local stdout: {process.stdout.strip()}")
        if process.stderr:
            print_warning(f"Local stderr: {process.stderr.strip()}")
        return process
    except subprocess.CalledProcessError as e:
        # Use display_command here too for error logging if it was sensitive
        err_msg = f"Local command failed: {display_command}\nError: {e}\nStdout: {e.stdout}\nStderr: {e.stderr}"
        print_error(err_msg)
        raise  # Re-raise to be handled by the caller or stop the script

def generate_ssh_key():
    """
    Generates an SSH key pair (id_rsa_k8s_auto, id_rsa_k8s_auto.pub) locally
    if it doesn't exist. Uses a default name to avoid overwriting user's id_rsa.
    """
    if not os.path.exists(SSH_KEY_PATH):
        print_info(f"Generating SSH key pair at {SSH_KEY_PATH}...")
        run_local_command(f'ssh-keygen -t rsa -b 4096 -f {SSH_KEY_PATH} -N "" -C "k8s_installer_auto_key"')
        print_info("SSH key pair generated.")
    else:
        print_info(f"SSH key pair already exists at {SSH_KEY_PATH}.")

def copy_ssh_key_to_server(server_ip, username, password):
    """Copies the SSH public key to a server using ssh-copy-id or Paramiko."""
    print_info(f"Copying SSH key to {username}@{server_ip}...")
    try:
        # Attempt to use ssh-copy-id first, as it's robust
        # This requires sshpass to be installed for non-interactive password input
        # Mark sensitive_output=True to hide password in logs
        run_local_command(
            f"sshpass -p '{password}' ssh-copy-id -i {SSH_PUBLIC_KEY_PATH} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null {username}@{server_ip}",
            check=True,
            sensitive_output=True
        )
        print_info(f"SSH key copied successfully to {server_ip} using ssh-copy-id.")
        return True # Indicates success
    except FileNotFoundError: # Raised by subprocess.run if sshpass isn't found
        print_warning("'sshpass' command not found. Will attempt Paramiko method for key copying.")
    except subprocess.CalledProcessError as e:
        # ssh-copy-id might fail for various reasons (e.g., password incorrect, SSH server misconfiguration)
        print_warning(f"ssh-copy-id failed for {server_ip}. Error: {e.stderr.strip()}. Will attempt Paramiko method.")

    # Fallback to Paramiko if ssh-copy-id fails or is not available
    print_info(f"Attempting to copy SSH key to {server_ip} using Paramiko method...")
    ssh = None
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # Provide a timeout for the connection attempt
        ssh.connect(server_ip, username=username, password=password, timeout=20, banner_timeout=20, auth_timeout=20)

        with open(SSH_PUBLIC_KEY_PATH, 'r') as key_file:
            public_key = key_file.read().strip()

        # Ensure no duplicate keys are added
        check_key_cmd = f"grep -qF '{public_key}' ~/.ssh/authorized_keys"
        stdin, stdout, stderr = ssh.exec_command(check_key_cmd)
        key_exists = stdout.channel.recv_exit_status() == 0

        if key_exists:
            print_info(f"Public key already exists in authorized_keys on {server_ip}.")
        else:
            print_info(f"Adding public key to authorized_keys on {server_ip}.")
            commands = [
                "mkdir -p ~/.ssh",
                f"echo '{public_key}' >> ~/.ssh/authorized_keys",
                "chmod 700 ~/.ssh",
                "chmod 600 ~/.ssh/authorized_keys"
            ]
            for cmd_idx, cmd in enumerate(commands):
                print_info(f"Executing on {server_ip} (Paramiko fallback, step {cmd_idx+1}/{len(commands)}): {cmd}")
                stdin, stdout, stderr = ssh.exec_command(cmd)
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    stderr_output = stderr.read().decode().strip()
                    print_error(f"Paramiko: Failed to execute '{cmd}' on {server_ip}. Exit: {exit_status}. Stderr: {stderr_output}")
                    return False # Indicates failure
        print_info(f"SSH key setup via Paramiko appears successful for {server_ip}.")
        return True # Indicates success
    except paramiko.AuthenticationException:
        print_error(f"Paramiko: Authentication failed when trying to copy key to {username}@{server_ip}. Incorrect password?")
        return False
    except paramiko.SSHException as e:
        print_error(f"Paramiko: SSH connection error when trying to copy key to {server_ip}: {e}")
        return False
    except Exception as e:
        print_error(f"Paramiko: An unexpected error occurred while copying SSH key to {server_ip}: {e}")
        return False
    finally:
        if ssh:
            ssh.close()


def run_remote_command(server_ip, username, command, pty=False, hide=False, timeout=300):
    """
    Runs a command on a remote server using Paramiko with the generated SSH key.
    Includes a timeout for the command execution.
    """
    if not hide:
        print_info(f"Executing on {username}@{server_ip}: {command}")
    ssh_client = None
    ssh_client = None
    try:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        # Increased timeouts for connect as well
        ssh_client.connect(server_ip, username=username, key_filename=SSH_KEY_PATH, timeout=20, banner_timeout=20, auth_timeout=20)

        # Execute command with a channel timeout
        channel = ssh_client.get_transport().open_session()
        channel.settimeout(timeout) # Timeout for the channel operations
        channel.get_pty(term='vt100' if pty else '') # Get PTY if requested
        channel.exec_command(command)

        # Read output
        # Reading stdout/stderr can block if the command produces a lot of output
        # and the buffer fills up. For very long running commands or large outputs,
        # a more sophisticated non-blocking read or select-based approach would be needed.
        # For typical setup commands, this should be okay.
        output = channel.recv(1024*1024).decode('utf-8', 'ignore').strip() # Read up to 1MB
        error_output = channel.recv_stderr(1024*1024).decode('utf-8', 'ignore').strip()
        exit_status = channel.recv_exit_status() # Wait for command to finish

        if not hide and output:
            print_info(f"Stdout from {server_ip} for '{command[:50]}...':\n{output}")
        if not hide and error_output:
            print_warning(f"Stderr from {server_ip} for '{command[:50]}...':\n{error_output}")

        if exit_status != 0:
            full_error_message = f"Command '{command}' failed on {server_ip} with exit status {exit_status}."
            # Output and error_output are already printed if not hidden
            raise subprocess.CalledProcessError(exit_status, command, output, error_output)
        return output, error_output
    except paramiko.AuthenticationException:
        err_msg = f"Authentication failed for {username}@{server_ip} using key {SSH_KEY_PATH}. Ensure passwordless SSH is correctly set up."
        print_error(err_msg)
        raise ConnectionError(err_msg) # Raise a more specific error
    except paramiko.SSHException as e: # Catches various SSH protocol errors, timeouts during connect
        err_msg = f"SSH connection error for {username}@{server_ip}: {e}"
        print_error(err_msg)
        raise ConnectionError(err_msg)
    except socket.timeout: # This can be raised by channel.recv if channel.settimeout is hit
        err_msg = f"Timeout executing command '{command}' on {server_ip} (>{timeout}s)."
        print_error(err_msg)
        raise TimeoutError(err_msg)
    except Exception as e:
        err_msg = f"Failed to execute command '{command}' on {server_ip}: {type(e).__name__} {e}"
        print_error(err_msg)
        raise # Re-raise to be caught by callers if needed
    finally:
        if ssh_client:
            ssh_client.close()

# --- Installation Steps ---

def execute_remote_commands(server_ip, username, commands_to_run, step_name):
    """Helper to execute a list of commands for a step, with error checking."""
    print_info(f"Starting step: '{step_name}' on {server_ip}...")
    for i, cmd in enumerate(commands_to_run):
        try:
            # Add a more specific timeout for apt commands if needed, otherwise use default.
            cmd_timeout = 600 if "apt-get" in cmd or "apt " in cmd else 300
            run_remote_command(server_ip, username, cmd, timeout=cmd_timeout)
        except (subprocess.CalledProcessError, ConnectionError, TimeoutError, Exception) as e:
            print_error(f"Failed command {i+1}/{len(commands_to_run)} ('{cmd[:60]}...') in step '{step_name}' on {server_ip}.")
            # Error already printed by run_remote_command or its callers
            # Decide if this error is critical for this step
            raise # Re-raise to abort this server's setup or entire script
    print_info(f"Successfully completed step: '{step_name}' on {server_ip}.")


def common_prerequisites(server_ip, username):
    """Disables swap, configures kernel modules and sysctl settings."""
    commands = [
        "sudo swapoff -a",
        r"sudo sed -i '/ swap / s/^\(.*\)$/#\1/g' /etc/fstab", # Persist swap off, raw string
        "cat <<EOF | sudo tee /etc/modules-load.d/k8s.conf\noverlay\nbr_netfilter\nEOF",
        "sudo modprobe overlay",
        "sudo modprobe br_netfilter",
        "cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf\nnet.bridge.bridge-nf-call-iptables  = 1\nnet.bridge.bridge-nf-call-ip6tables = 1\nnet.ipv4.ip_forward                 = 1\nEOF",
        "sudo sysctl --system"
    ]
    execute_remote_commands(server_ip, username, commands, "Common Prerequisites")

def install_base_packages(server_ip, username):
    """Installs base packages like apt-transport-https, ca-certificates, curl, etc."""
    # Update apt first, then install packages. This is critical.
    update_cmd = "sudo apt-get update -y"
    install_cmd = "sudo apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release"
    try:
        print_info(f"Updating package list on {server_ip}...")
        run_remote_command(server_ip, username, update_cmd, timeout=600) # Longer timeout for update
        print_info(f"Installing base packages on {server_ip}...")
        run_remote_command(server_ip, username, install_cmd, timeout=600) # Longer timeout for install
    except (subprocess.CalledProcessError, ConnectionError, TimeoutError) as e:
        print_error(f"Failed to install base packages on {server_ip}. This is a critical step.")
        raise
    print_info(f"Base packages installed on {server_ip}.")


def install_container_runtime(server_ip, username):
    """Installs and configures containerd."""
    # Docker GPG and repo setup (containerd.io comes from Docker repos)
    # Using pkgs.k8s.io for CRI tools if needed, but containerd.io is simpler for now.
    commands_repo_setup = [
        # Ensure directory for keyrings exists
        "sudo install -m 0755 -d /etc/apt/keyrings",
        # Add Docker's official GPG key
        "curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg",
        "sudo chmod a+r /etc/apt/keyrings/docker.gpg", # Ensure readable
        # Set up the stable repository for Docker
        'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null',
        "sudo apt-get update -y" # Update after adding new repo
    ]
    execute_remote_commands(server_ip, username, commands_repo_setup, "Containerd Repo Setup")

    commands_install_configure = [
        "sudo apt-get install -y containerd.io",
        "sudo mkdir -p /etc/containerd", # Ensure config directory exists
        # Generate default config and enable SystemdCgroup
        "sudo bash -c 'containerd config default > /etc/containerd/config.toml'",
        "sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/g' /etc/containerd/config.toml",
        "sudo systemctl restart containerd",
        "sudo systemctl enable containerd"
    ]
    execute_remote_commands(server_ip, username, commands_install_configure, "Containerd Install & Configure")

def install_kubeadm_kubelet_kubectl(server_ip, username):
    """Installs Kubernetes components: kubeadm, kubelet, kubectl."""
    # Kubernetes GPG and repo setup
    # Adapted from https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/install-kubeadm/
    k8s_version = "v1.29" # Define version to use consistently
    commands_repo_setup = [
        "sudo apt-get update -y", # Should have been run by base_packages, but good to ensure
        # Ensure directory for keyrings exists (again, for safety)
        "sudo install -m 0755 -d /etc/apt/keyrings",
        f"curl -fsSL https://pkgs.k8s.io/core:/stable:/{k8s_version}/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg",
        f'echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/{k8s_version}/deb/ /" | sudo tee /etc/apt/sources.list.d/kubernetes.list',
        "sudo apt-get update -y" # Update after adding new repo
    ]
    execute_remote_commands(server_ip, username, commands_repo_setup, "Kubernetes Repo Setup")

    commands_install = [
        "sudo apt-get install -y kubelet kubeadm kubectl",
        "sudo apt-mark hold kubelet kubeadm kubectl" # Pin versions
    ]
    execute_remote_commands(server_ip, username, commands_install, "Kubernetes Components Install")


def initialize_master_node(master_ip, username, pod_network_cidr):
    """Initializes the first master node. Returns the join command."""
    print_info(f"Initializing Kubernetes master on {master_ip}...")
    # Kubeadm init can take a while, especially image pulls.
    # Use a longer timeout. --upload-certs is good for joining other control planes or rejoining.
    init_command = f"sudo kubeadm init --pod-network-cidr={pod_network_cidr} --upload-certs"
    raw_output = ""
    try:
        # pty=True can help with commands that expect a terminal.
        # Hide verbose output and parse it.
        raw_output, _ = run_remote_command(master_ip, username, init_command, pty=True, hide=True, timeout=900) # 15 min timeout for kubeadm init
        print_info(f"Kubeadm init completed on {master_ip}.")
        # Full output can be very long, print summary or on error.
        # print_info(f"Kubeadm init raw output from {master_ip}:\n{raw_output}")

    except subprocess.CalledProcessError as e:
        # kubeadm init might output the join command even on certain "errors" or warnings.
        # So, we capture its output for parsing regardless.
        print_warning(f"kubeadm init on {master_ip} had a non-zero exit status ({e.returncode}). Review output carefully.")
        raw_output = str(e.stdout) + "\n" + str(e.stderr) # Ensure string conversion
        # If it's a real failure, the join command might not be there.
    except TimeoutError:
        print_error(f"kubeadm init timed out on {master_ip}. Check network connectivity and image pull progress on the node.")
        return None # Indicate failure
    except (ConnectionError, Exception) as e:
        print_error(f"Failed to execute kubeadm init on {master_ip}: {e}")
        return None

    if not raw_output:
        print_error(f"No output received from kubeadm init on {master_ip}.")
        return None

    # Parse the join command (can be multi-line)
    join_command_full = ""
    join_command_lines = []
    capture_join_command = False
    # A more robust way to find the join command:
    # Look for "kubeadm join <ip>:<port>" and then the subsequent lines with token and hash.

    # Split raw_output by lines for easier processing
    lines = raw_output.splitlines()
    for i, line in enumerate(lines):
        if "kubeadm join" in line and ("--token" in lines[i+1] if i+1 < len(lines) else False): # A bit more specific
            # Found the start of a potential join command block
            capture_join_command = True

        if capture_join_command:
            # Remove leading/trailing whitespace and backslashes
            processed_line = line.strip().rstrip('\\').strip()
            if processed_line: # Add if not empty
                join_command_lines.append(processed_line)

            # The join command usually ends with the discovery-token-ca-cert-hash line
            # or if the next line doesn't look like part of the command (e.g., not indented, no backslash).
            if "discovery-token-ca-cert-hash" in line:
                break # Found the complete join command section
            if not line.strip().endswith('\\') and len(join_command_lines) > 1 : # if it's not a continuation line and we have something
                 # Check if the next line starts a new thought or is empty, heuristic
                if i + 1 < len(lines) and ("kubeadm join" in lines[i+1] or not lines[i+1].strip().startswith("--")):
                    # likely end of current join command block if next line is new join or not an option
                    if any("discovery-token-ca-cert-hash" in l for l in join_command_lines): # check if we got the hash
                        break
                    else: # reset if we didn't get the hash, maybe it was a false positive.
                        join_command_lines = []
                        capture_join_command = False


    if not join_command_lines or not any("discovery-token-ca-cert-hash" in l for l in join_command_lines):
        print_error(f"Could not reliably parse kubeadm join command from output on {master_ip}.")
        print_warning("Full kubeadm init output for debugging:")
        for line_num, text_line in enumerate(raw_output.splitlines()):
            print_warning(f"  Line {line_num}: {text_line}")
        return None # Indicate failure

    join_command_full = " ".join(join_command_lines)
    if not join_command_full.strip().startswith("sudo"): # Ensure it's a sudo command
        join_command_full = "sudo " + join_command_full.strip()

    # Add verbosity to join command for debugging worker joins
    join_command_full += " --v=5"
    print_info(f"Successfully parsed join command: {join_command_full}")

    # Setup kubeconfig for the user on the master node
    kubeconfig_commands = [
        "mkdir -p $HOME/.kube",
        "sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config", # -i to prompt if overwriting, though unlikely here
        "sudo chown $(id -u):$(id -g) $HOME/.kube/config"
    ]
    try:
        execute_remote_commands(master_ip, username, kubeconfig_commands, "Setup Kubeconfig on Master")
    except Exception as e:
        print_error(f"Failed to set up kubeconfig on master {master_ip}: {e}")
        return None # Kubeconfig is essential for CNI install etc.

    print_info(f"Kubernetes master initialized on {master_ip}. Kubeconfig ready for user {username}.")
    return join_command_full

def install_cni_plugin(master_ip, username, cni_manifest_url):
    """Installs the CNI plugin on the master node using kubectl."""
    print_info(f"Installing CNI plugin from {cni_manifest_url} on master {master_ip}...")
    # This command uses kubectl, which needs $HOME/.kube/config to be present and correct.
    cni_command = f"kubectl apply -f {cni_manifest_url}"

    # Wait for the API server to be fully ready after init. This can take a moment.
    # A better way would be to poll `kubectl get componentstatuses` or similar.
    print_info("Waiting for Kubernetes API server to be ready before installing CNI (approx 30s)...")
    time.sleep(30)

    try:
        # Use a reasonable timeout for kubectl apply
        run_remote_command(master_ip, username, cni_command, timeout=120)
        print_info(f"CNI plugin manifest applied successfully from {cni_manifest_url} on {master_ip}.")
        print_info("It may take a few minutes for CNI pods to be fully operational. Monitor with 'kubectl get pods -A -w'.")
    except (subprocess.CalledProcessError, ConnectionError, TimeoutError) as e:
        print_error(f"Failed to apply CNI plugin manifest on {master_ip}.")
        # Error details already printed by run_remote_command
        print_warning("Please check cluster status ('kubectl get nodes', 'kubectl get pods -A') on the master node.")
        print_warning("Common CNI issues: Pod CIDR mismatch, network policies blocking CNI pods, or issues with the CNI manifest itself.")
        # Attempt to get some debug info
        try:
            run_remote_command(master_ip, username, "kubectl get nodes -o wide", timeout=60)
            run_remote_command(master_ip, username, "kubectl get pods -A -o wide", timeout=120)
        except Exception as debug_e:
            print_warning(f"Could not retrieve debug info (nodes/pods) from cluster: {debug_e}")
        raise # Re-raise to indicate CNI installation failure


def join_worker_node(worker_ip, username, join_command):
    """Joins a worker node to the Kubernetes cluster."""
    print_info(f"Attempting to join worker node {worker_ip} to the cluster...")
    if not join_command or "kubeadm join" not in join_command:
        print_error(f"Invalid or empty join command provided for worker {worker_ip}. Cannot join.")
        return False # Indicate failure

    try:
        # Kubeadm join can also take some time (image pulls). Use pty and longer timeout.
        # The join command already includes verbosity (--v=5) from master init parsing.
        run_remote_command(worker_ip, username, join_command, pty=True, timeout=600) # 10 min timeout
        print_info(f"Worker node {worker_ip} successfully executed join command.")
        print_info(f"Run 'kubectl get nodes' on the master to verify worker status.")
        return True # Indicate success
    except (subprocess.CalledProcessError, ConnectionError, TimeoutError) as e:
        print_error(f"Failed to join worker node {worker_ip} to the cluster.")
        # Error details already printed by run_remote_command
        print_warning("Common reasons for join failure:")
        print_warning(f"- Network connectivity issues between worker {worker_ip} and master (check firewalls, routing, DNS).")
        print_warning(f"- Master's API server port (usually 6443) not reachable from {worker_ip}.")
        print_warning("- Incorrect join command (e.g., token expired - default is 24h, CA hash mismatch).")
        print_warning(f"- Container runtime not properly installed or running on worker {worker_ip}.")
        print_warning(f"- Swap not disabled on worker {worker_ip}.")
        print_warning(f"- Kubelet issues on {worker_ip}. Check with 'journalctl -xeu kubelet' and 'sudo systemctl status kubelet' on the worker.")
        return False # Indicate failure
    except Exception as e: # Catch any other unexpected error
        print_error(f"An unexpected error occurred while joining worker {worker_ip}: {e}")
        return False


# --- Main Logic ---
def get_user_inputs():
    """Gets necessary inputs from the user, with basic validation."""
    print_info("--- Kubernetes Cluster Setup ---")
    # Simplified to one master for this script's scope
    num_masters = 1
    print_info("This script will set up 1 master node.")

    master_ips = []
    while True:
        ip = input(f"Enter IP address for master node 1: ").strip()
        if not ip:
            print_warning("IP address cannot be empty.")
            continue
        # Basic IP validation (does not check reachability or if it's a valid format for all cases)
        if len(ip.split('.')) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in ip.split('.')):
            master_ips.append(ip)
            break
        else:
            print_warning(f"'{ip}' does not look like a valid IPv4 address. Please re-enter.")

    while True:
        try:
            num_workers_str = input("Enter the number of worker nodes (e.g., 1, or 0 if none): ").strip()
            num_workers = int(num_workers_str)
            if num_workers < 0:
                print_warning("Number of worker nodes cannot be negative.")
            else:
                break
        except ValueError:
            print_warning(f"Invalid input '{num_workers_str}'. Please enter a number (e.g., 0, 1, 2).")

    worker_ips = []
    for i in range(num_workers):
        while True:
            ip = input(f"Enter IP address for worker node {i+1}: ").strip()
            if not ip:
                print_warning("IP address cannot be empty.")
                continue
            if ip in master_ips or ip in worker_ips:
                print_warning(f"IP address {ip} has already been entered. Please use unique IPs.")
                continue
            if len(ip.split('.')) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in ip.split('.')):
                worker_ips.append(ip)
                break
            else:
                print_warning(f"'{ip}' does not look like a valid IPv4 address. Please re-enter.")

    ssh_username = ""
    while not ssh_username:
        ssh_username = input("Enter the SSH username for all servers (non-root with sudo privileges): ").strip()
        if not ssh_username:
            print_warning("SSH username cannot be empty.")

    server_passwords = {}
    all_ips = master_ips + worker_ips

    if not all_ips: # Should not happen due to master IP being mandatory
        print_critical_error("No server IPs provided. Exiting.")

    use_same_password_input = ""
    while use_same_password_input not in ['yes', 'no']:
        use_same_password_input = input("Use the same SSH password for all servers? (yes/no): ").strip().lower()
        if use_same_password_input not in ['yes', 'no']:
            print_warning("Invalid input. Please enter 'yes' or 'no'.")

    if use_same_password_input == 'yes':
        first_password = ""
        while not first_password: # Ensure password is not empty
            first_password = getpass.getpass(f"Enter SSH password for {ssh_username} on ALL servers: ")
            if not first_password: print_warning("Password cannot be empty.")
        for ip in all_ips:
            server_passwords[ip] = first_password
    else:
        for ip in all_ips:
            current_password = ""
            while not current_password:
                current_password = getpass.getpass(f"Enter SSH password for {ssh_username}@{ip}: ")
                if not current_password: print_warning("Password cannot be empty.")
            server_passwords[ip] = current_password

    # Basic check for duplicate IPs across all lists
    if len(all_ips) != len(set(all_ips)):
        print_critical_error("Duplicate IP addresses detected in the input. Please ensure all IPs are unique.")

    return master_ips, worker_ips, ssh_username, server_passwords


def main():
    try:
        master_ips, worker_ips, ssh_username, server_passwords = get_user_inputs()
    except Exception as e: # Catch any unexpected error during input
        print_critical_error(f"An error occurred during user input: {e}")
        return # Exit if inputs fail critically

    all_server_ips = master_ips + worker_ips

    if not all_server_ips: # Should be caught by get_user_inputs, but as a safeguard
        print_info("No servers specified. Exiting.")
        return

    print_info("Starting Kubernetes cluster installation...")
    successful_ssh_setup_servers = []

    # Step 1: Generate SSH keys locally
    try:
        generate_ssh_key()
    except Exception as e:
        print_critical_error(f"Failed to generate local SSH key: {e}. Aborting.")
        return

    # Step 2: Copy SSH key to all servers for passwordless login
    print_info("--- Setting up passwordless SSH ---")
    try:
        # Check for sshpass availability for a better ssh-copy-id experience
        run_local_command("sshpass -V", check=False, sensitive_output=True)
    except FileNotFoundError:
        print_warning("`sshpass` is not installed locally. The script will use a Python-based SSH key copy method which might be less robust.")
        print_warning("For a smoother experience, consider installing sshpass: `sudo apt-get install sshpass` (on this machine).")
    except subprocess.CalledProcessError:
        print_warning("`sshpass -V` failed, continuing with Python-based SSH key copy.")


    for server_ip in all_server_ips:
        password = server_passwords[server_ip]
        if not copy_ssh_key_to_server(server_ip, ssh_username, password):
            print_error(f"Failed to copy SSH key to {server_ip}. This server will be skipped. Check logs for details.")
            continue # Skip this server if key copy fails

        time.sleep(1) # Brief pause for SSH daemon
        try:
            print_info(f"Testing passwordless SSH to {server_ip}...")
            run_remote_command(server_ip, ssh_username, "echo 'Passwordless SSH successful.'", timeout=30)
            successful_ssh_setup_servers.append(server_ip)
        except (ConnectionError, TimeoutError, subprocess.CalledProcessError) as e:
            print_error(f"Failed to establish passwordless SSH to {server_ip} after copying key. This server will be skipped.")
            # Error details already printed by run_remote_command
        except Exception as e: # Catch any other unexpected error during test
            print_error(f"An unexpected error occurred while testing passwordless SSH to {server_ip}: {e}. This server will be skipped.")

    if not successful_ssh_setup_servers:
        print_critical_error("SSH setup failed for all servers. Aborting installation.")
        return

    # Filter master_ips and worker_ips to only include those with successful SSH setup
    master_ips = [ip for ip in master_ips if ip in successful_ssh_setup_servers]
    worker_ips = [ip for ip in worker_ips if ip in successful_ssh_setup_servers]

    if not master_ips:
        print_critical_error("SSH setup failed for all designated master nodes. Cannot proceed.")
        return

    current_all_server_ips = master_ips + worker_ips # Update list of servers to process

    # Step 3: Install prerequisites and Kubernetes components on all nodes
    failed_nodes_prereqs = []
    for server_ip in current_all_server_ips:
        print_info(f"--- Configuring node: {server_ip} ---")
        try:
            common_prerequisites(server_ip, ssh_username)
            install_base_packages(server_ip, ssh_username) # Includes initial apt update
            install_container_runtime(server_ip, ssh_username)
            install_kubeadm_kubelet_kubectl(server_ip, ssh_username)
            print_info(f"--- Node base configuration finished successfully for: {server_ip} ---")
        except (subprocess.CalledProcessError, ConnectionError, TimeoutError) as e:
            print_error(f"Critical error during base configuration of node {server_ip}. This node will be skipped.")
            failed_nodes_prereqs.append(server_ip)
        except Exception as e: # Catch any other unexpected error
            print_error(f"An unexpected critical error occurred during base configuration of {server_ip}: {e}. This node will be skipped.")
            failed_nodes_prereqs.append(server_ip)

    # Update lists again based on successful prerequisite installation
    master_ips = [ip for ip in master_ips if ip not in failed_nodes_prereqs]
    worker_ips = [ip for ip in worker_ips if ip not in failed_nodes_prereqs]

    if not master_ips:
        print_critical_error("Base configuration failed for all designated master nodes. Cannot initialize cluster.")
        return

    # Step 4: Initialize the first master node
    # For simplicity, script picks the first available master from the filtered list.
    first_master_ip = master_ips[0]
    print_info(f"--- Initializing Master Node: {first_master_ip} ---")
    join_command = None
    try:
        join_command = initialize_master_node(first_master_ip, ssh_username, DEFAULT_POD_NETWORK_CIDR)
        if not join_command:
            # initialize_master_node should print specific errors
            print_critical_error(f"Failed to initialize master node {first_master_ip} or retrieve join command. Aborting cluster setup.")
            return
    except Exception as e: # Catch unexpected errors from init function itself
         print_critical_error(f"An unexpected error occurred during master initialization of {first_master_ip}: {e}. Aborting.")
         return

    # Step 5: Install CNI plugin on the master node
    try:
        install_cni_plugin(first_master_ip, ssh_username, CALICO_MANIFEST_URL)
    except Exception as e:
        # CNI failure is serious but we might still want to know about workers
        print_error(f"Failed to install CNI plugin on master {first_master_ip}. The cluster might not be fully functional. Error: {e}")
        # Proceed to inform about worker status if any workers were configured.

    # Step 6: Join worker nodes (if any workers are left after filtering)
    successful_workers = []
    if worker_ips:
        print_info(f"--- Joining Worker Nodes to Master {first_master_ip} ---")
        if "kubeadm join" not in join_command: # Double check validity
             print_error(f"The retrieved join command seems invalid: '{join_command}'. Cannot join workers.")
        else:
            for worker_ip in worker_ips:
                print_info(f"--- Attempting to join worker: {worker_ip} ---")
                if join_worker_node(worker_ip, ssh_username, join_command):
                    successful_workers.append(worker_ip)
                    time.sleep(5) # Small delay between joining nodes
                else:
                    print_warning(f"Worker node {worker_ip} failed to join. See previous errors.")
    elif successful_ssh_setup_servers and not worker_ips and any(ip in worker_ips for ip in successful_ssh_setup_servers):
        # This case means workers were specified, but all failed before join stage
        print_warning("No worker nodes available to join (all failed prior steps or were not specified).")
    else:
        print_info("No worker nodes specified or available to join.")

    print_info("--- Kubernetes Cluster Installation Script Finished ---")
    print_info(f"Master node is: {first_master_ip} (Initialization attempted/completed).")
    if successful_workers:
        print_info(f"Successfully joined worker nodes: {', '.join(successful_workers)}")
    if len(worker_ips) > len(successful_workers):
        failed_join_workers = [ip for ip in worker_ips if ip not in successful_workers]
        if failed_join_workers:
            print_warning(f"Worker nodes that failed to join: {', '.join(failed_join_workers)}")

    if failed_nodes_prereqs:
        print_warning(f"Nodes that failed during prerequisite/base setup and were skipped: {', '.join(failed_nodes_prereqs)}")

    skipped_ssh_nodes = [ip for ip in (master_ips + worker_ips) if ip not in successful_ssh_setup_servers] # Original lists before filtering
    original_all_ips = server_passwords.keys() # Get all originally intended IPs
    skipped_ssh_nodes = [ip for ip in original_all_ips if ip not in successful_ssh_setup_servers]
    if skipped_ssh_nodes:
         print_warning(f"Nodes skipped due to SSH key copy or test failure: {', '.join(skipped_ssh_nodes)}")


    print_info("\n--- Post-Installation Information ---")
    print_info(f"To manage your cluster from the master node ({first_master_ip}):")
    print_info(f"1. SSH into the master: `ssh {ssh_username}@{first_master_ip} -i {SSH_KEY_PATH}`")
    print_info(f"2. Kubectl is configured for user '{ssh_username}'. Try: `kubectl get nodes`, `kubectl get pods -A`")

    print_info(f"\nTo manage your cluster from your local machine (where this script ran):")
    print_info(f"1. Ensure kubectl is installed locally (https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/).")
    print_info(f"2. Copy the kubeconfig from the master:")
    print_info(f"   mkdir -p $HOME/.kube")
    print_info(f"   scp -i {SSH_KEY_PATH} {ssh_username}@{first_master_ip}:~/.kube/config $HOME/.kube/k8s_cluster_{first_master_ip.replace('.', '_')}_config")
    print_info(f"3. Set KUBECONFIG environment variable or merge with existing config:")
    print_info(f"   export KUBECONFIG=$HOME/.kube/k8s_cluster_{first_master_ip.replace('.', '_')}_config")
    print_info(f"   (Or add to your ~/.bashrc or ~/.zshrc for persistence)")
    print_info(f"4. Test with: `kubectl get nodes`")
    print_info("Review all logs above for any warnings or errors for specific nodes.")

if __name__ == "__main__":
    # Wrap main in a try-except to catch any unhandled exceptions at the very top level
    try:
        main()
    except KeyboardInterrupt:
        print_info("\nInstallation process interrupted by user. Exiting.")
        sys.exit(1)
    except Exception as e:
        print_critical_error(f"A critical unhandled error occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
