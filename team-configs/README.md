# Legacy repository examples

Runtime credentials are **not read from this directory**.

On the execution VM, Cursor reads only:

```text
/config/<team>.config
/config/kubeconfig
/config/vss-cli-secret.yaml
/config/backend-secret.yaml
```

Do not place real credentials in this repository.
