# Shared single-metric KPI builder. Keeping the common Perses/Query Builder
# envelope here makes KPI semantics reviewable without duplicating the same
# panel boilerplate across every dashboard.
locals {
  dashboard_metric_kpis = {
    "c0ac575a-c5be-4392-9d53-2c78f7315159" = {
      dashboard         = "host"
      name              = "Host CPU utilization"
      description       = "Current non-idle CPU as a percentage of the selected host's total capacity."
      unit              = "percentunit"
      decimal_precision = "1"
      metric_name       = "system.cpu.utilization"
      time_aggregation  = "avg"
      space_aggregation = "sum"
      reduce_to         = "last"
      filter            = "host.name IN $host_name AND state != 'idle'"
    }
    "e51bca2f-9a00-4807-97c4-f2bec18670a6" = {
      dashboard         = "host"
      name              = "Used memory"
      description       = "Current host memory used, excluding free, cache, and buffers."
      unit              = "bytes"
      decimal_precision = "2"
      metric_name       = "system.memory.usage"
      time_aggregation  = "avg"
      space_aggregation = "sum"
      reduce_to         = "last"
      filter            = "host.name IN $host_name AND state = 'used'"
    }
    "d81ef4a1-1694-48c5-b111-fa706334d883" = {
      dashboard         = "host"
      name              = "Load average (1m)"
      description       = "Current one-minute load average across selected hosts."
      unit              = "short"
      decimal_precision = "2"
      metric_name       = "system.cpu.load_average.1m"
      time_aggregation  = "avg"
      space_aggregation = "max"
      reduce_to         = "last"
      filter            = "host.name IN $host_name"
    }
    "f1017d6a-35a4-4220-9bf2-0e65a907114d" = {
      dashboard         = "host"
      name              = "Max filesystem utilization"
      description       = "Current utilization of the fullest selected filesystem."
      unit              = "percentunit"
      decimal_precision = "1"
      metric_name       = "system.filesystem.utilization"
      time_aggregation  = "avg"
      space_aggregation = "max"
      reduce_to         = "last"
      filter            = "host.name IN $host_name"
    }

    "a08e8dc8-efde-4dbb-a237-c484adf6cce5" = {
      dashboard         = "containers"
      name              = "Total CPU cores"
      description       = "Logical CPU cores used by selected Compose services, averaged over the latest interval."
      unit              = "short"
      decimal_precision = "2"
      metric_name       = "container.cpu.usage.total"
      time_aggregation  = "rate"
      space_aggregation = "sum"
      reduce_to         = "last"
      filter            = "lakehouse.compose.service IN $compose_service"
      formula           = "A / 1000000000"
    }
    "2940fec6-f705-41d4-b65b-9631144f6f26" = {
      dashboard         = "containers"
      name              = "Total memory used"
      description       = "Current memory used by selected Compose services, excluding cache."
      unit              = "bytes"
      decimal_precision = "2"
      metric_name       = "container.memory.usage.total"
      time_aggregation  = "avg"
      space_aggregation = "sum"
      reduce_to         = "last"
      filter            = "lakehouse.compose.service IN $compose_service"
    }
    "61ee482c-72cd-427f-82c8-9a81a3fe4df9" = {
      dashboard         = "containers"
      name              = "Max memory utilization"
      description       = "Highest current memory-to-limit percentage among selected containers."
      unit              = "percent"
      decimal_precision = "1"
      metric_name       = "container.memory.percent"
      time_aggregation  = "avg"
      space_aggregation = "max"
      reduce_to         = "last"
      filter            = "container.name IN $container_name"
    }

    "59de448c-857c-48eb-93ab-688134e834c8" = {
      dashboard         = "postgres"
      name              = "Average commits/s"
      description       = "Mean commit throughput over the selected time range."
      unit              = "ops"
      decimal_precision = "2"
      metric_name       = "postgresql.commits"
      time_aggregation  = "rate"
      space_aggregation = "sum"
      reduce_to         = "avg"
      filter            = "postgresql.database.name IN $database"
    }
    "6f5a4a96-9362-43a5-b833-2908dbc8b9a5" = {
      dashboard         = "postgres"
      name              = "Active backends"
      description       = "Current backend processes across selected databases."
      unit              = "short"
      decimal_precision = "0"
      metric_name       = "postgresql.backends"
      time_aggregation  = "avg"
      space_aggregation = "sum"
      reduce_to         = "last"
      filter            = "postgresql.database.name IN $database"
    }
    "630b6a99-cd80-428e-b9ea-69fce3db5e7b" = {
      dashboard         = "postgres"
      name              = "Connection limit"
      description       = "Configured PostgreSQL maximum client connections."
      unit              = "short"
      decimal_precision = "0"
      metric_name       = "postgresql.connection.max"
      time_aggregation  = "avg"
      space_aggregation = "max"
      reduce_to         = "last"
      filter            = ""
    }
    "e96ec35d-c025-4c5b-9d5a-1b38f55add54" = {
      dashboard         = "postgres"
      name              = "Database size"
      description       = "Current total size of selected databases."
      unit              = "bytes"
      decimal_precision = "2"
      metric_name       = "postgresql.db_size"
      time_aggregation  = "avg"
      space_aggregation = "sum"
      reduce_to         = "last"
      filter            = "postgresql.database.name IN $database"
    }

    "0fe443a4-a61d-442b-b5cf-b05ee3f094da" = {
      dashboard         = "airflow"
      name              = "Scheduler loop p95"
      description       = "Recent 95th-percentile scheduler loop duration."
      unit              = "ms"
      decimal_precision = "2"
      metric_name       = "airflow.scheduler.scheduler_loop_duration.bucket"
      time_aggregation  = ""
      space_aggregation = "p95"
      reduce_to         = "last"
      filter            = ""
    }
    "eb5f72d8-b12c-48e7-b7ce-22f776845ed5" = {
      dashboard         = "airflow"
      name              = "Running tasks"
      description       = "Current task instances running in the executor."
      unit              = "short"
      decimal_precision = "0"
      metric_name       = "airflow.executor.running_tasks"
      time_aggregation  = "avg"
      space_aggregation = "sum"
      reduce_to         = "last"
      filter            = ""
    }
    "8c225e28-555b-45af-b293-c14686f0991f" = {
      dashboard         = "airflow"
      name              = "Queued tasks"
      description       = "Current tasks waiting for executor capacity."
      unit              = "short"
      decimal_precision = "0"
      metric_name       = "airflow.executor.queued_tasks"
      time_aggregation  = "avg"
      space_aggregation = "sum"
      reduce_to         = "last"
      filter            = ""
    }
    "a95a470f-6179-4323-b66e-d302d0274ef7" = {
      dashboard         = "airflow"
      name              = "Open executor slots"
      description       = "Current executor capacity available for additional tasks."
      unit              = "short"
      decimal_precision = "0"
      metric_name       = "airflow.executor.open_slots"
      time_aggregation  = "avg"
      space_aggregation = "sum"
      reduce_to         = "last"
      filter            = ""
    }
  }

  dashboard_metric_kpi_builder_specs = {
    for id, kpi in local.dashboard_metric_kpis : id => {
      metrics = {
        name     = "A"
        signal   = "metrics"
        disabled = lookup(kpi, "formula", "") != ""
        aggregations = [
          {
            metric_name       = kpi.metric_name
            time_aggregation  = kpi.time_aggregation
            space_aggregation = kpi.space_aggregation
            reduce_to         = kpi.reduce_to
          },
        ]
        filter = {
          expression = kpi.filter
        }
        having = {
          expression = ""
        }
        limit = lookup(kpi, "formula", "") == "" ? 100 : 10000
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
  }

  dashboard_metric_kpi_panels = {
    for id, kpi in local.dashboard_metric_kpis : id => {
      kind = "Panel"
      spec = {
        display = {
          name        = kpi.name
          description = kpi.description
        }
        links = []
        plugin = {
          number_panel = {
            kind = "signoz/NumberPanel"
            spec = {
              formatting = {
                unit              = kpi.unit
                decimal_precision = kpi.decimal_precision
              }
            }
          }
        }
        queries = [
          {
            kind = "scalar"
            spec = {
              name = "A"
              plugin = merge(
                lookup(kpi, "formula", "") == "" ? {
                  builder_query = {
                    kind = "signoz/BuilderQuery"
                    spec = local.dashboard_metric_kpi_builder_specs[id]
                  }
                } : {},
                lookup(kpi, "formula", "") != "" ? {
                  composite_query = {
                    kind = "signoz/CompositeQuery"
                    spec = {
                      queries = [
                        {
                          builder_query = {
                            type = "builder_query"
                            spec = local.dashboard_metric_kpi_builder_specs[id]
                          }
                        },
                        {
                          builder_formula = {
                            type = "builder_formula"
                            spec = {
                              name       = "F1"
                              expression = lookup(kpi, "formula", "")
                              disabled   = false
                              having = {
                                expression = ""
                              }
                              legend = ""
                              limit  = 100
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
                  }
                } : {},
              )
            }
          },
        ]
      }
    }
  }
}
