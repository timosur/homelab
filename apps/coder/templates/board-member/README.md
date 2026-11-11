# Board-member Coder workspace template

This template creates one independently owned development workspace and 100 GiB Synology iSCSI
home volume for each Coder workspace. The volume survives a workspace stop, while the Pod is
created only while the workspace is running, on either approved worker in the shared power pool.

Do not publish this template until the staged power coordination in
[`../../power-coordination/README.md`](../../power-coordination/README.md) is deployed. Its
authenticated preflight must succeed before workspace storage or its Pod can be created.

Validate and publish from this directory after logging in to the private Coder instance:

```sh
terraform init
terraform validate
./publish.sh
```

The template pins the Coder workspace image by digest. It requires the Coder `github` external-auth
provider before a workspace can be created; it does not mount a personal GitHub credential.

`publish.sh` sets Coder's default autostop and activity bump to 30 minutes. Each detected workspace
session activity extends its deadline by 30 minutes; without activity, Coder stops the workspace
after 30 minutes while its home PVC remains.
