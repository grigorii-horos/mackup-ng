# Syncing private keys

By default private keys for OpenSSH and GnuPG are NOT sycned.
You can sync your private keys if you want.
For example, to sync your entire OpenSSH `.ssh` directory,
create a `~/.mackup/applications/ssh.toml` file with the following content:

```toml
name = "SSH"
files = [
    ".ssh",
]
```
