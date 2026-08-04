locals {
  name = "tgbao-dev-services"
  tags = {
    Environment = "dev"
    ManagedBy   = "terraform"
    Project     = "tgbao"
    Component   = "services"
  }
}

data "terraform_remote_state" "tailscale" {
  backend = "s3"
  config = {
    bucket  = var.state_bucket
    key     = "lakehouse/tailscale/dev/terraform.tfstate"
    region  = "ap-southeast-1"
    encrypt = true
  }
}

data "oci_identity_availability_domains" "available" {
  compartment_id = var.tenancy_ocid
}

resource "oci_core_vcn" "services" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = ["10.42.0.0/16"]
  display_name   = local.name
  dns_label      = "lakehousedev"
  freeform_tags  = local.tags
}

resource "oci_core_internet_gateway" "services" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.services.id
  display_name   = local.name
  enabled        = true
  freeform_tags  = local.tags
}

resource "oci_core_route_table" "services" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.services.id
  display_name   = local.name
  freeform_tags  = local.tags

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.services.id
  }
}

resource "oci_core_security_list" "services" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.services.id
  display_name   = local.name
  freeform_tags  = local.tags

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }
}

resource "oci_core_subnet" "services" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.services.id
  cidr_block        = "10.42.1.0/24"
  display_name      = local.name
  dns_label         = "services"
  route_table_id    = oci_core_route_table.services.id
  security_list_ids = [oci_core_security_list.services.id]
  freeform_tags     = local.tags
}

resource "oci_core_instance" "services" {
  availability_domain  = data.oci_identity_availability_domains.available.availability_domains[0].name
  compartment_id       = var.compartment_ocid
  display_name         = local.name
  shape                = "VM.Standard.A1.Flex"
  preserve_boot_volume = false
  freeform_tags        = local.tags

  shape_config {
    ocpus         = 4
    memory_in_gbs = 24
  }

  source_details {
    source_type             = "image"
    source_id               = var.image_ocid
    boot_volume_size_in_gbs = 100
    boot_volume_vpus_per_gb = 10
  }

  create_vnic_details {
    assign_public_ip = true
    display_name     = local.name
    hostname_label   = local.name
    subnet_id        = oci_core_subnet.services.id
  }

  metadata = {
    user_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
      hostname           = local.name
      installer          = indent(6, file("${path.module}/../../../../runtime/identity/install-aws-signing-helper"))
      tailscale_auth_key = jsonencode(data.terraform_remote_state.tailscale.outputs.services_auth_key)
    }))
  }
}
