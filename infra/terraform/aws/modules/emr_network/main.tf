data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_region" "current" {}

locals {
  availability_zones = slice(data.aws_availability_zones.available.names, 0, 2)
  public_subnets = {
    for index, availability_zone in local.availability_zones :
    availability_zone => cidrsubnet(var.vpc_cidr, 8, index)
  }
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(var.tags, {
    Name = var.name_prefix
  })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = var.name_prefix
  })
}

resource "aws_subnet" "public" {
  for_each = local.public_subnets

  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = true
  vpc_id                  = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-${each.key}"
    Tier = "public"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-public"
  })
}

resource "aws_route" "internet" {
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
  route_table_id         = aws_route_table.public.id
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  route_table_id = aws_route_table.public.id
  subnet_id      = each.value.id
}

resource "aws_vpc_endpoint" "s3" {
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  vpc_id            = aws_vpc.this.id
  route_table_ids   = [aws_route_table.public.id]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-s3"
  })
}

resource "aws_security_group" "workers" {
  name        = "${var.name_prefix}-workers"
  description = "Network boundary for EMR Serverless workers."
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-workers"
  })
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.workers.id
  description       = "HTTPS to AWS services and external data sources."
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  ip_protocol       = "tcp"
  to_port           = 443
}
