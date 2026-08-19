# Ops alert policy: every rule lists its notification channels directly (the
# provider has no channel resources; the operator channel is created once in the
# SigNoz UI and named through the signoz_alert_channels variable). Routes only
# need editing when a severity deserves a separate destination.

resource "signoz_rule" "host_disk_70_percent" {
  alert      = "host_filesystem_70_percent"
  alert_type = "METRIC_BASED_ALERT"
  rule_type  = "threshold_rule"

  description = "Host filesystem utilization exceeded 70% of capacity."

  annotations = {
    summary     = "{{$labels.mountpoint}} on {{$labels.host_name}} at {{$value}} of capacity"
    description = "Filesystem {{$labels.mountpoint}} on host {{$labels.host_name}} is over 70% full."
  }

  labels = {
    severity = "warning"
    team     = "platform"
  }

  condition = {
    composite_query = {
      panel_type = "table"
      query_type = "builder"
      unit       = "percentunit"

      queries = [
        {
          builder_query = {
            type = "builder_query"
            spec = {
              metrics = {
                name   = "A"
                signal = "metrics"

                aggregations = [
                  {
                    metric_name       = "system.filesystem.utilization"
                    time_aggregation  = "avg"
                    space_aggregation = "max"
                  },
                ]
                limit = 100
                order = [
                  {
                    key = {
                      name = "max(avg(system.filesystem.utilization))"
                    }
                    direction = "desc"
                  },
                ]
              }
            }
          }
        },
      ]
    }

    selected_query_name = "A"

    thresholds = {
      basic = {
        kind = "basic"
        spec = [
          {
            channels   = local.alert_channels
            match_type = "at_least_once"
            name       = "warning"
            op         = "above"
            target     = 0.7
          },
        ]
      }
    }
  }

  evaluation = {
    rolling = {
      kind = "rolling"
      spec = {
        eval_window = "15m"
        frequency   = "5m"
      }
    }
  }

  notification_settings = {
    group_by = ["mountpoint", "host.name"]
  }

  schema_version = "v2alpha1"
}

resource "signoz_rule" "host_disk_80_percent" {
  alert      = "host_filesystem_80_percent"
  alert_type = "METRIC_BASED_ALERT"
  rule_type  = "threshold_rule"

  description = "Host filesystem utilization exceeded 80% of capacity."

  annotations = {
    summary     = "{{$labels.mountpoint}} on {{$labels.host_name}} at {{$value}} of capacity"
    description = "Filesystem {{$labels.mountpoint}} on host {{$labels.host_name}} is over 80% full."
  }

  labels = {
    severity = "critical"
    team     = "platform"
  }

  condition = {
    composite_query = {
      panel_type = "table"
      query_type = "builder"
      unit       = "percentunit"

      queries = [
        {
          builder_query = {
            type = "builder_query"
            spec = {
              metrics = {
                name   = "A"
                signal = "metrics"

                aggregations = [
                  {
                    metric_name       = "system.filesystem.utilization"
                    time_aggregation  = "avg"
                    space_aggregation = "max"
                  },
                ]
                limit = 100
                order = [
                  {
                    key = {
                      name = "max(avg(system.filesystem.utilization))"
                    }
                    direction = "desc"
                  },
                ]
              }
            }
          }
        },
      ]
    }

    selected_query_name = "A"

    thresholds = {
      basic = {
        kind = "basic"
        spec = [
          {
            channels   = local.alert_channels
            match_type = "at_least_once"
            name       = "critical"
            op         = "above"
            target     = 0.8
          },
        ]
      }
    }
  }

  evaluation = {
    rolling = {
      kind = "rolling"
      spec = {
        eval_window = "15m"
        frequency   = "5m"
      }
    }
  }

  notification_settings = {
    group_by = ["mountpoint", "host.name"]
  }

  schema_version = "v2alpha1"
}

resource "signoz_rule" "host_disk_90_percent" {
  alert      = "host_filesystem_90_percent"
  alert_type = "METRIC_BASED_ALERT"
  rule_type  = "threshold_rule"

  description = "Host filesystem utilization exceeded 90% of capacity; immediate action required."

  annotations = {
    summary     = "{{$labels.mountpoint}} on {{$labels.host_name}} at {{$value}} of capacity"
    description = "Filesystem {{$labels.mountpoint}} on host {{$labels.host_name}} is over 90% full."
  }

  labels = {
    severity = "emergency"
    team     = "platform"
  }

  condition = {
    composite_query = {
      panel_type = "table"
      query_type = "builder"
      unit       = "percentunit"

      queries = [
        {
          builder_query = {
            type = "builder_query"
            spec = {
              metrics = {
                name   = "A"
                signal = "metrics"

                aggregations = [
                  {
                    metric_name       = "system.filesystem.utilization"
                    time_aggregation  = "avg"
                    space_aggregation = "max"
                  },
                ]
                limit = 100
                order = [
                  {
                    key = {
                      name = "max(avg(system.filesystem.utilization))"
                    }
                    direction = "desc"
                  },
                ]
              }
            }
          }
        },
      ]
    }

    selected_query_name = "A"

    thresholds = {
      basic = {
        kind = "basic"
        spec = [
          {
            channels   = local.alert_channels
            match_type = "at_least_once"
            name       = "emergency"
            op         = "above"
            target     = 0.9
          },
        ]
      }
    }
  }

  evaluation = {
    rolling = {
      kind = "rolling"
      spec = {
        eval_window = "15m"
        frequency   = "5m"
      }
    }
  }

  notification_settings = {
    group_by = ["mountpoint", "host.name"]
  }

  schema_version = "v2alpha1"
}

# Absent-data alerts stand in for collector drops and endpoint liveness: the
# collection agent does not export its own internal metrics anywhere outside
# its container, so ingest-path failures surface as missing series. If any of
# these fire, verify the collection agent container and signoz-ingester ports
# before suspecting the workload itself.

resource "signoz_rule" "host_metrics_missing" {
  alert      = "host_metrics_missing"
  alert_type = "METRIC_BASED_ALERT"
  rule_type  = "threshold_rule"

  description = "No hostmetrics arrived for 15 minutes; the collection agent or the OTLP ingest path is down."

  annotations = {
    summary     = "Host metrics stopped arriving for {{$labels.host_name}}"
    description = "system.cpu.utilization has not reported for 15 minutes on {{$labels.host_name}}."
  }

  labels = {
    severity = "critical"
    team     = "platform"
  }

  condition = {
    alert_on_absent = true
    absent_for      = 15

    composite_query = {
      panel_type = "table"
      query_type = "builder"

      queries = [
        {
          builder_query = {
            type = "builder_query"
            spec = {
              metrics = {
                name   = "A"
                signal = "metrics"

                aggregations = [
                  {
                    metric_name       = "system.cpu.utilization"
                    time_aggregation  = "avg"
                    space_aggregation = "max"
                  },
                ]
                limit = 100
                order = [
                  {
                    key = {
                      name = "max(avg(system.cpu.utilization))"
                    }
                    direction = "desc"
                  },
                ]
              }
            }
          }
        },
      ]
    }

    selected_query_name = "A"

    thresholds = {
      basic = {
        kind = "basic"
        spec = [
          {
            channels   = local.alert_channels
            match_type = "at_least_once"
            name       = "missing"
            op         = "below"
            target     = 0
          },
        ]
      }
    }
  }

  evaluation = {
    rolling = {
      kind = "rolling"
      spec = {
        eval_window = "15m"
        frequency   = "5m"
      }
    }
  }

  notification_settings = {
    group_by = ["host.name"]
  }

  schema_version = "v2alpha1"
}

resource "signoz_rule" "metadata_postgres_metrics_missing" {
  alert      = "metadata_postgres_metrics_missing"
  alert_type = "METRIC_BASED_ALERT"
  rule_type  = "threshold_rule"

  description = "No postgresql receiver metrics arrived for 15 minutes; the metadata server or the pg_monitor scraper is down."

  annotations = {
    summary     = "PostgreSQL metrics stopped arriving"
    description = "postgresql.backends has not reported for 15 minutes; check the lakehouse_monitor scrape and the metadata-postgres container."
  }

  labels = {
    severity = "warning"
    team     = "platform"
  }

  condition = {
    alert_on_absent = true
    absent_for      = 15

    composite_query = {
      panel_type = "table"
      query_type = "builder"

      queries = [
        {
          builder_query = {
            type = "builder_query"
            spec = {
              metrics = {
                name   = "A"
                signal = "metrics"

                aggregations = [
                  {
                    metric_name       = "postgresql.backends"
                    time_aggregation  = "avg"
                    space_aggregation = "max"
                  },
                ]
                limit = 100
                order = [
                  {
                    key = {
                      name = "max(avg(postgresql.backends))"
                    }
                    direction = "desc"
                  },
                ]
              }
            }
          }
        },
      ]
    }

    selected_query_name = "A"

    thresholds = {
      basic = {
        kind = "basic"
        spec = [
          {
            channels   = local.alert_channels
            match_type = "at_least_once"
            name       = "missing"
            op         = "below"
            target     = 0
          },
        ]
      }
    }
  }

  evaluation = {
    rolling = {
      kind = "rolling"
      spec = {
        eval_window = "15m"
        frequency   = "5m"
      }
    }
  }

  notification_settings = {}

  schema_version = "v2alpha1"
}

resource "signoz_rule" "airflow_scheduler_heartbeat_missing" {
  alert      = "airflow_scheduler_heartbeat_missing"
  alert_type = "METRIC_BASED_ALERT"
  rule_type  = "threshold_rule"

  description = "No Airflow scheduler heartbeat arrived for 5 minutes; the scheduler or its OTLP export is down."

  annotations = {
    summary     = "Airflow scheduler heartbeat stopped"
    description = "airflow.scheduler_heartbeat has not reported for 5 minutes."
  }

  labels = {
    severity = "critical"
    team     = "platform"
  }

  condition = {
    alert_on_absent = true
    absent_for      = 5

    composite_query = {
      panel_type = "table"
      query_type = "builder"

      queries = [
        {
          builder_query = {
            type = "builder_query"
            spec = {
              metrics = {
                name   = "A"
                signal = "metrics"

                aggregations = [
                  {
                    metric_name       = "airflow.scheduler_heartbeat"
                    time_aggregation  = "rate"
                    space_aggregation = "sum"
                  },
                ]
                limit = 100
                order = [
                  {
                    key = {
                      name = "sum(rate(airflow.scheduler_heartbeat))"
                    }
                    direction = "desc"
                  },
                ]
              }
            }
          }
        },
      ]
    }

    selected_query_name = "A"

    thresholds = {
      basic = {
        kind = "basic"
        spec = [
          {
            channels   = local.alert_channels
            match_type = "at_least_once"
            name       = "missing"
            op         = "below"
            target     = 0.01
          },
        ]
      }
    }
  }

  evaluation = {
    rolling = {
      kind = "rolling"
      spec = {
        eval_window = "5m"
        frequency   = "1m"
      }
    }
  }

  notification_settings = {}

  schema_version = "v2alpha1"
}

resource "signoz_rule" "airflow_task_completions_stalled" {
  alert      = "airflow_task_completions_stalled"
  alert_type = "METRIC_BASED_ALERT"
  rule_type  = "threshold_rule"

  description = "No task instances finished (success or failure) for 2 hours; DAGs may be stuck or the scheduler idle beyond expectations."

  annotations = {
    summary     = "No Airflow task completions in 2 hours"
    description = "airflow.ti_successes + airflow.ti_failures has not incremented for 2 hours."
  }

  labels = {
    severity = "warning"
    team     = "platform"
  }

  condition = {
    composite_query = {
      panel_type = "table"
      query_type = "builder"

      queries = [
        {
          builder_query = {
            type = "builder_query"
            spec = {
              metrics = {
                name     = "A"
                signal   = "metrics"
                disabled = true

                aggregations = [
                  {
                    metric_name       = "airflow.ti_successes"
                    time_aggregation  = "increase"
                    space_aggregation = "sum"
                  },
                ]
                limit = 10000
                order = [
                  {
                    key = {
                      name = "sum(increase(airflow.ti_successes))"
                    }
                    direction = "desc"
                  },
                ]
              }
            }
          }
        },
        {
          builder_query = {
            type = "builder_query"
            spec = {
              metrics = {
                name     = "B"
                signal   = "metrics"
                disabled = true

                aggregations = [
                  {
                    metric_name       = "airflow.ti_failures"
                    time_aggregation  = "increase"
                    space_aggregation = "sum"
                  },
                ]
                limit = 10000
                order = [
                  {
                    key = {
                      name = "sum(increase(airflow.ti_failures))"
                    }
                    direction = "desc"
                  },
                ]
              }
            }
          }
        },
        {
          builder_formula = {
            type = "builder_formula"
            spec = {
              name       = "F1"
              expression = "A + B"
              limit      = 100
              order = [
                {
                  key = {
                    name = "__result"
                  }
                  direction = "desc"
                },
              ]
            }
          }
        },
      ]
    }

    selected_query_name = "F1"

    thresholds = {
      basic = {
        kind = "basic"
        spec = [
          {
            channels   = local.alert_channels
            match_type = "in_total"
            name       = "stalled"
            op         = "below"
            target     = 1
          },
        ]
      }
    }
  }

  evaluation = {
    rolling = {
      kind = "rolling"
      spec = {
        eval_window = "2h"
        frequency   = "15m"
      }
    }
  }

  notification_settings = {}

  schema_version = "v2alpha1"
}

resource "signoz_rule" "metadata_backup_failed" {
  alert      = "metadata_backup_failed"
  alert_type = "LOGS_BASED_ALERT"
  rule_type  = "threshold_rule"

  description = "Any metadata backup audit line reported status=failure."

  annotations = {
    summary     = "Metadata backup failed for {{$labels.database}}"
    description = "The backup script wrote {{$value}} failure audit line(s) for database {{$labels.database}} (slot {{$labels.slot}})."
  }

  labels = {
    severity = "critical"
    team     = "platform"
  }

  condition = {
    composite_query = {
      panel_type = "table"
      query_type = "builder"

      queries = [
        {
          builder_query = {
            type = "builder_query"
            spec = {
              logs = {
                name   = "A"
                signal = "logs"

                aggregations = [
                  {
                    expression = "count()"
                  },
                ]
                limit = 100
                order = [
                  {
                    key = {
                      name = "count()"
                    }
                    direction = "desc"
                  },
                ]

                filter = {
                  expression = "status = 'failure'"
                }

                group_by = [
                  {
                    name            = "database"
                    field_context   = "attribute"
                    field_data_type = "string"
                  },
                ]
              }
            }
          }
        },
      ]
    }

    selected_query_name = "A"

    thresholds = {
      basic = {
        kind = "basic"
        spec = [
          {
            channels   = local.alert_channels
            match_type = "at_least_once"
            name       = "failure recorded"
            op         = "above"
            target     = 0
          },
        ]
      }
    }
  }

  evaluation = {
    rolling = {
      kind = "rolling"
      spec = {
        eval_window = "30m"
        frequency   = "5m"
      }
    }
  }

  notification_settings = {
    group_by = ["database", "slot"]
  }

  schema_version = "v2alpha1"
}

resource "signoz_rule" "metadata_backup_airflow_missing" {
  alert      = "metadata_backup_airflow_missing"
  alert_type = "LOGS_BASED_ALERT"
  rule_type  = "threshold_rule"

  description = "No successful backup audit line for the airflow database within the 15h window (12h cadence plus 30min jitter margin); the backup timer, script, or the audit log tail is broken."

  annotations = {
    summary     = "No successful metadata backup for airflow in 15h"
    description = "The 12-hour metadata backup cadence produced no success line for database airflow within 15 hours."
  }

  labels = {
    severity = "critical"
    team     = "platform"
  }

  condition = {
    composite_query = {
      panel_type = "table"
      query_type = "builder"

      queries = [
        {
          builder_query = {
            type = "builder_query"
            spec = {
              logs = {
                name   = "A"
                signal = "logs"

                aggregations = [
                  {
                    expression = "count()"
                  },
                ]
                limit = 100
                order = [
                  {
                    key = {
                      name = "count()"
                    }
                    direction = "desc"
                  },
                ]

                filter = {
                  expression = "status = 'success' AND database = 'airflow'"
                }
              }
            }
          }
        },
      ]
    }

    selected_query_name = "A"

    thresholds = {
      basic = {
        kind = "basic"
        spec = [
          {
            channels   = local.alert_channels
            match_type = "in_total"
            name       = "missed slot"
            op         = "below"
            target     = 1
          },
        ]
      }
    }
  }

  evaluation = {
    rolling = {
      kind = "rolling"
      spec = {
        eval_window = "15h"
        frequency   = "30m"
      }
    }
  }

  notification_settings = {
    renotify = {
      enabled      = true
      interval     = "4h"
      alert_states = ["firing"]
    }
  }

  schema_version = "v2alpha1"
}

resource "signoz_rule" "metadata_backup_lightdash_missing" {
  alert      = "metadata_backup_lightdash_missing"
  alert_type = "LOGS_BASED_ALERT"
  rule_type  = "threshold_rule"

  description = "No successful backup audit line for the lightdash database within the 15h window (12h cadence plus 30min jitter margin); the backup timer, script, or the audit log tail is broken."

  annotations = {
    summary     = "No successful metadata backup for lightdash in 15h"
    description = "The 12-hour metadata backup cadence produced no success line for database lightdash within 15 hours."
  }

  labels = {
    severity = "critical"
    team     = "platform"
  }

  condition = {
    composite_query = {
      panel_type = "table"
      query_type = "builder"

      queries = [
        {
          builder_query = {
            type = "builder_query"
            spec = {
              logs = {
                name   = "A"
                signal = "logs"

                aggregations = [
                  {
                    expression = "count()"
                  },
                ]
                limit = 100
                order = [
                  {
                    key = {
                      name = "count()"
                    }
                    direction = "desc"
                  },
                ]

                filter = {
                  expression = "status = 'success' AND database = 'lightdash'"
                }
              }
            }
          }
        },
      ]
    }

    selected_query_name = "A"

    thresholds = {
      basic = {
        kind = "basic"
        spec = [
          {
            channels   = local.alert_channels
            match_type = "in_total"
            name       = "missed slot"
            op         = "below"
            target     = 1
          },
        ]
      }
    }
  }

  evaluation = {
    rolling = {
      kind = "rolling"
      spec = {
        eval_window = "15h"
        frequency   = "30m"
      }
    }
  }

  notification_settings = {
    renotify = {
      enabled      = true
      interval     = "4h"
      alert_states = ["firing"]
    }
  }

  schema_version = "v2alpha1"
}

# No route policy is declared here: every rule threshold carries its own channel
# list (local.alert_channels), which is how the operator channel reaches alerts.
# A catch-all route policy would need an expression that is guaranteed to match
# every alert, which is not part of the provider's documented surface today.
