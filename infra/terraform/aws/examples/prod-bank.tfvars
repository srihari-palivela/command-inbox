name                           = "ci-prod-examplebank"
region                         = "eu-west-2"
azs                            = ["eu-west-2a", "eu-west-2b", "eu-west-2c"]
cluster_node_security_group_id = "sg-0123456789abcdef0"
admin_role_arns                = ["arn:aws:iam::111111111111:role/platform-admin"]
app_role_arns                  = ["arn:aws:iam::111111111111:role/ci-prod-examplebank-app"]
