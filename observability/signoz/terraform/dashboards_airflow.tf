# Airflow dashboard: Airflow's native OpenTelemetry metrics (prefix "airflow",
# orchestration/deploy/compose.yaml: AIRFLOW__METRICS__OTEL_ON) plus its traces.
# Resource-scoped fields (service.name, deployment.environment) use the resource
# context; Airflow stats tags (dag_id, pool, state) are metric attributes.

resource "signoz_dashboard" "airflow" {
  schema_version = "v6"
  name           = "lakehouse-airflow"
  tags = [
    {
      key   = "tag"
      value = "airflow"
    },
    {
      key   = "tag"
      value = "orchestration"
    },
    {
      key   = "tag"
      value = "metrics"
    },
    {
      key   = "tag"
      value = "traces"
    },
    {
      key   = "tag"
      value = "dev"
    },
  ]

  spec = {
    display = {
      name        = "Airflow"
      description = "Scheduler heartbeat, task success/failure, DAG run durations, and pool slots from Airflow native OpenTelemetry metrics and traces."
    }
    links     = []
    variables = []
    panels = {
      "d7862908-c2b0-5858-a228-a22c1d7e78c1" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Scheduler heartbeats"
            description = "Scheduler heartbeats per interval; a flat zero means the scheduler stopped reporting."
          }
          links = []
          plugin = {
            time_series_panel = {
              kind = "signoz/TimeSeriesPanel"
              spec = {
                visualization = {
                  time_preference = "global_time"
                  fill_spans      = false
                }
                formatting = {
                  unit              = "short"
                  decimal_precision = "2"
                }
                chart_appearance = {
                  line_interpolation = "spline"
                  show_points        = false
                  line_style         = "solid"
                  fill_mode          = "none"
                  span_gaps = {
                    fill_only_below = false
                    fill_less_than  = "0s"
                  }
                }
                axes = {
                  soft_min     = 0
                  is_log_scale = false
                }
                legend = {
                  position = "bottom"
                  mode     = "list"
                }
              }
            }
          }
          queries = [
            {
              kind = "time_series"
              spec = {
                name = "A"
                plugin = {
                  builder_query = {
                    kind = "signoz/BuilderQuery"
                    spec = {
                      metrics = {
                        name   = "A"
                        signal = "metrics"
                        aggregations = [
                          {
                            metric_name       = "airflow.scheduler_heartbeat"
                            time_aggregation  = "increase"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = ""
                        }
                        group_by = [
                          {
                            name            = "service.name"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{service.name}} heartbeats"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(increase(airflow.scheduler_heartbeat))"
                            }
                            direction = "desc"
                          },
                        ]
                      }
                    }
                  }
                }
              }
            },
          ]
        }
      }
      "b385fc0f-4042-568b-af49-17051fe26d2b" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Task outcome counts"
            description = "Task instance successes and failures per interval (low-volume counter; per-interval counts avoid sub-unit rates)."
          }
          links = []
          plugin = {
            time_series_panel = {
              kind = "signoz/TimeSeriesPanel"
              spec = {
                visualization = {
                  time_preference = "global_time"
                  fill_spans      = false
                }
                formatting = {
                  unit              = "short"
                  decimal_precision = "2"
                }
                chart_appearance = {
                  line_interpolation = "spline"
                  show_points        = false
                  line_style         = "solid"
                  fill_mode          = "none"
                  span_gaps = {
                    fill_only_below = false
                    fill_less_than  = "0s"
                  }
                }
                axes = {
                  soft_min     = 0
                  is_log_scale = false
                }
                legend = {
                  position = "bottom"
                  mode     = "list"
                }
              }
            }
          }
          queries = [
            {
              kind = "time_series"
              spec = {
                name = "A"
                plugin = {
                  composite_query = {
                    kind = "signoz/CompositeQuery"
                    spec = {
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
                                    metric_name       = "airflow.ti_successes"
                                    time_aggregation  = "increase"
                                    space_aggregation = "sum"
                                  },
                                ]
                                filter = {
                                  expression = "dag_id EXISTS"
                                }
                                group_by = [
                                  {
                                    name            = "dag_id"
                                    field_context   = "attribute"
                                    field_data_type = "string"
                                  },
                                  {
                                    name            = "task_id"
                                    field_context   = "attribute"
                                    field_data_type = "string"
                                  },
                                ]
                                having = {
                                  expression = ""
                                }
                                legend = "{{dag_id}} - {{task_id}} (success)"
                                limit  = 100
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
                                name   = "B"
                                signal = "metrics"
                                aggregations = [
                                  {
                                    metric_name       = "airflow.ti_failures"
                                    time_aggregation  = "increase"
                                    space_aggregation = "sum"
                                  },
                                ]
                                filter = {
                                  expression = "dag_id EXISTS"
                                }
                                group_by = [
                                  {
                                    name            = "dag_id"
                                    field_context   = "attribute"
                                    field_data_type = "string"
                                  },
                                  {
                                    name            = "task_id"
                                    field_context   = "attribute"
                                    field_data_type = "string"
                                  },
                                ]
                                having = {
                                  expression = ""
                                }
                                legend = "{{dag_id}} - {{task_id}} (failure)"
                                limit  = 100
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
                      ]
                    }
                  }
                }
              }
            },
          ]
        }
      }
      "416bd861-923c-5a86-8f46-4db6a2f29308" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Task duration p50/p95"
            description = "Task duration quantiles in milliseconds, grouped by operation state."
          }
          links = []
          plugin = {
            time_series_panel = {
              kind = "signoz/TimeSeriesPanel"
              spec = {
                visualization = {
                  time_preference = "global_time"
                  fill_spans      = false
                }
                formatting = {
                  unit              = "ms"
                  decimal_precision = "2"
                }
                chart_appearance = {
                  line_interpolation = "spline"
                  show_points        = false
                  line_style         = "solid"
                  fill_mode          = "none"
                  span_gaps = {
                    fill_only_below = false
                    fill_less_than  = "0s"
                  }
                }
                axes = {
                  soft_min     = 0
                  is_log_scale = false
                }
                legend = {
                  position = "bottom"
                  mode     = "list"
                }
              }
            }
          }
          queries = [
            {
              kind = "time_series"
              spec = {
                name = "A"
                plugin = {
                  composite_query = {
                    kind = "signoz/CompositeQuery"
                    spec = {
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
                                    metric_name       = "airflow.task.duration"
                                    time_aggregation  = "avg"
                                    space_aggregation = "p50"
                                  },
                                ]
                                filter = {
                                  expression = "dag_id EXISTS"
                                }
                                group_by = [
                                  {
                                    name            = "dag_id"
                                    field_context   = "attribute"
                                    field_data_type = "string"
                                  },
                                  {
                                    name            = "task_id"
                                    field_context   = "attribute"
                                    field_data_type = "string"
                                  },
                                ]
                                having = {
                                  expression = ""
                                }
                                legend = "{{dag_id}} - {{task_id}} (p50)"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "p50(avg(airflow.task.duration))"
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
                                name   = "B"
                                signal = "metrics"
                                aggregations = [
                                  {
                                    metric_name       = "airflow.task.duration"
                                    time_aggregation  = "avg"
                                    space_aggregation = "p95"
                                  },
                                ]
                                filter = {
                                  expression = "dag_id EXISTS"
                                }
                                group_by = [
                                  {
                                    name            = "dag_id"
                                    field_context   = "attribute"
                                    field_data_type = "string"
                                  },
                                  {
                                    name            = "task_id"
                                    field_context   = "attribute"
                                    field_data_type = "string"
                                  },
                                ]
                                having = {
                                  expression = ""
                                }
                                legend = "{{dag_id}} - {{task_id}} (p95)"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "p95(avg(airflow.task.duration))"
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
                  }
                }
              }
            },
          ]
        }
      }
      "94351dca-3cbd-5a86-a195-b68c51332bc8" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "DAG run duration by DAG"
            description = "Successful DAG run durations in seconds, grouped by dag_id."
          }
          links = []
          plugin = {
            time_series_panel = {
              kind = "signoz/TimeSeriesPanel"
              spec = {
                visualization = {
                  time_preference = "global_time"
                  fill_spans      = false
                }
                formatting = {
                  unit              = "s"
                  decimal_precision = "2"
                }
                chart_appearance = {
                  line_interpolation = "spline"
                  show_points        = false
                  line_style         = "solid"
                  fill_mode          = "none"
                  span_gaps = {
                    fill_only_below = false
                    fill_less_than  = "0s"
                  }
                }
                axes = {
                  soft_min     = 0
                  is_log_scale = false
                }
                legend = {
                  position = "bottom"
                  mode     = "list"
                }
              }
            }
          }
          queries = [
            {
              kind = "time_series"
              spec = {
                name = "A"
                plugin = {
                  builder_query = {
                    kind = "signoz/BuilderQuery"
                    spec = {
                      metrics = {
                        name   = "A"
                        signal = "metrics"
                        aggregations = [
                          {
                            metric_name       = "airflow.dagrun.duration.success"
                            time_aggregation  = "avg"
                            space_aggregation = "avg"
                          },
                        ]
                        filter = {
                          expression = "dag_id EXISTS"
                        }
                        group_by = [
                          {
                            name            = "dag_id"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{dag_id}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "avg(avg(airflow.dagrun.duration.success))"
                            }
                            direction = "desc"
                          },
                        ]
                      }
                    }
                  }
                }
              }
            },
          ]
        }
      }
      "c517d114-b7f4-5e94-9306-68ebd42103cb" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "DAG run failures"
            description = "Failed DAG run count rate by dag_id."
          }
          links = []
          plugin = {
            time_series_panel = {
              kind = "signoz/TimeSeriesPanel"
              spec = {
                visualization = {
                  time_preference = "global_time"
                  fill_spans      = false
                }
                formatting = {
                  unit              = "ops"
                  decimal_precision = "2"
                }
                chart_appearance = {
                  line_interpolation = "spline"
                  show_points        = false
                  line_style         = "solid"
                  fill_mode          = "none"
                  span_gaps = {
                    fill_only_below = false
                    fill_less_than  = "0s"
                  }
                }
                axes = {
                  soft_min     = 0
                  is_log_scale = false
                }
                legend = {
                  position = "bottom"
                  mode     = "list"
                }
              }
            }
          }
          queries = [
            {
              kind = "time_series"
              spec = {
                name = "A"
                plugin = {
                  builder_query = {
                    kind = "signoz/BuilderQuery"
                    spec = {
                      metrics = {
                        name   = "A"
                        signal = "metrics"
                        aggregations = [
                          {
                            metric_name       = "airflow.dagrun.duration.failed"
                            time_aggregation  = "count"
                            space_aggregation = "count"
                          },
                        ]
                        filter = {
                          expression = "dag_id EXISTS"
                        }
                        group_by = [
                          {
                            name            = "dag_id"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{dag_id}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "count(count(airflow.dagrun.duration.failed))"
                            }
                            direction = "desc"
                          },
                        ]
                      }
                    }
                  }
                }
              }
            },
          ]
        }
      }
      "f8d0fdea-1749-5fb2-ad7e-deb74b2aa099" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Running DAG runs"
            description = "Scheduler-reported running DAG run count."
          }
          links = []
          plugin = {
            time_series_panel = {
              kind = "signoz/TimeSeriesPanel"
              spec = {
                visualization = {
                  time_preference = "global_time"
                  fill_spans      = false
                }
                formatting = {
                  unit              = "short"
                  decimal_precision = "0"
                }
                chart_appearance = {
                  line_interpolation = "spline"
                  show_points        = false
                  line_style         = "solid"
                  fill_mode          = "none"
                  span_gaps = {
                    fill_only_below = false
                    fill_less_than  = "0s"
                  }
                }
                axes = {
                  soft_min     = 0
                  is_log_scale = false
                }
                legend = {
                  position = "bottom"
                  mode     = "list"
                }
              }
            }
          }
          queries = [
            {
              kind = "time_series"
              spec = {
                name = "A"
                plugin = {
                  builder_query = {
                    kind = "signoz/BuilderQuery"
                    spec = {
                      metrics = {
                        name   = "A"
                        signal = "metrics"
                        aggregations = [
                          {
                            metric_name       = "airflow.scheduler.dagruns.running"
                            time_aggregation  = "avg"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = ""
                        }
                        having = {
                          expression = ""
                        }
                        legend = "running DAG runs"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(avg(airflow.scheduler.dagruns.running))"
                            }
                            direction = "desc"
                          },
                        ]
                      }
                    }
                  }
                }
              }
            },
          ]
        }
      }
      "867e521d-f095-52c7-9440-af4273fd8691" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Recent traces"
            description = "Most recent Airflow spans (scheduler and task execution traces)."
          }
          links = []
          plugin = {
            list_panel = {
              kind = "signoz/ListPanel"
              spec = {}
            }
          }
          queries = [
            {
              kind = "raw"
              spec = {
                name = "A"
                plugin = {
                  builder_query = {
                    kind = "signoz/BuilderQuery"
                    spec = {
                      traces = {
                        name   = "A"
                        signal = "traces"
                        filter = {
                          expression = "service.name = 'airflow' AND dag_id EXISTS"
                        }
                        select_fields = [
                          {
                            name            = "timestamp"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                          {
                            name            = "name"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                          {
                            name            = "dag_id"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                          {
                            name            = "task_id"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                          {
                            name            = "duration_nano"
                            field_context   = "attribute"
                            field_data_type = "int64"
                          },
                          {
                            name            = "service.name"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                        ]
                        limit = 25
                        order = [
                          {
                            key = {
                              name = "timestamp"
                            }
                            direction = "desc"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                      }
                    }
                  }
                }
              }
            },
          ]
        }
      }
      "e687b321-4f2a-5b8c-9c71-7d1a5e93c102" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "DAG schedule delay"
            description = "Delay between scheduled execution time and actual start time in seconds, grouped by dag_id."
          }
          links = []
          plugin = {
            time_series_panel = {
              kind = "signoz/TimeSeriesPanel"
              spec = {
                visualization = {
                  time_preference = "global_time"
                  fill_spans      = false
                }
                formatting = {
                  unit              = "s"
                  decimal_precision = "2"
                }
                chart_appearance = {
                  line_interpolation = "spline"
                  show_points        = false
                  line_style         = "solid"
                  fill_mode          = "none"
                  span_gaps = {
                    fill_only_below = false
                    fill_less_than  = "0s"
                  }
                }
                axes = {
                  soft_min     = 0
                  is_log_scale = false
                }
                legend = {
                  position = "bottom"
                  mode     = "list"
                }
              }
            }
          }
          queries = [
            {
              kind = "time_series"
              spec = {
                name = "A"
                plugin = {
                  builder_query = {
                    kind = "signoz/BuilderQuery"
                    spec = {
                      metrics = {
                        name   = "A"
                        signal = "metrics"
                        aggregations = [
                          {
                            metric_name       = "airflow.dagrun.schedule_delay"
                            time_aggregation  = "avg"
                            space_aggregation = "avg"
                          },
                        ]
                        filter = {
                          expression = "dag_id EXISTS"
                        }
                        group_by = [
                          {
                            name            = "dag_id"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{dag_id}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "avg(avg(airflow.dagrun.schedule_delay))"
                            }
                            direction = "desc"
                          },
                        ]
                      }
                    }
                  }
                }
              }
            },
          ]
        }
      }
      "a218d943-7e1b-5f62-8c90-3b4e7f82d519" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Executor capacity and queue"
            description = "Active worker tasks, queued tasks waiting for execution, and open executor slots."
          }
          links = []
          plugin = {
            time_series_panel = {
              kind = "signoz/TimeSeriesPanel"
              spec = {
                visualization = {
                  time_preference = "global_time"
                  fill_spans      = false
                }
                formatting = {
                  unit              = "short"
                  decimal_precision = "0"
                }
                chart_appearance = {
                  line_interpolation = "spline"
                  show_points        = false
                  line_style         = "solid"
                  fill_mode          = "none"
                  span_gaps = {
                    fill_only_below = false
                    fill_less_than  = "0s"
                  }
                }
                axes = {
                  soft_min     = 0
                  is_log_scale = false
                }
                legend = {
                  position = "bottom"
                  mode     = "list"
                }
              }
            }
          }
          queries = [
            {
              kind = "time_series"
              spec = {
                name = "A"
                plugin = {
                  composite_query = {
                    kind = "signoz/CompositeQuery"
                    spec = {
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
                                    metric_name       = "airflow.executor.running_tasks"
                                    time_aggregation  = "avg"
                                    space_aggregation = "sum"
                                  },
                                ]
                                filter = {
                                  expression = ""
                                }
                                having = {
                                  expression = ""
                                }
                                legend = "running tasks"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "sum(avg(airflow.executor.running_tasks))"
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
                                name   = "B"
                                signal = "metrics"
                                aggregations = [
                                  {
                                    metric_name       = "airflow.executor.queued_tasks"
                                    time_aggregation  = "avg"
                                    space_aggregation = "sum"
                                  },
                                ]
                                filter = {
                                  expression = ""
                                }
                                having = {
                                  expression = ""
                                }
                                legend = "queued tasks"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "sum(avg(airflow.executor.queued_tasks))"
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
                                name   = "C"
                                signal = "metrics"
                                aggregations = [
                                  {
                                    metric_name       = "airflow.executor.open_slots"
                                    time_aggregation  = "avg"
                                    space_aggregation = "sum"
                                  },
                                ]
                                filter = {
                                  expression = ""
                                }
                                having = {
                                  expression = ""
                                }
                                legend = "open slots"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "sum(avg(airflow.executor.open_slots))"
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
                  }
                }
              }
            },
          ]
        }
      }
    }
    layouts = [
      {
        grid = {
          kind = "Grid"
          spec = {
            display = {
              title = "Overview and Concurrency"
              collapse = {
                open = true
              }
            }
            items = [
              {
                x      = 0
                y      = 0
                width  = 4
                height = 6
                content = {
                  ref = "#/spec/panels/d7862908-c2b0-5858-a228-a22c1d7e78c1"
                }
              },
              {
                x      = 4
                y      = 0
                width  = 4
                height = 6
                content = {
                  ref = "#/spec/panels/b385fc0f-4042-568b-af49-17051fe26d2b"
                }
              },
              {
                x      = 8
                y      = 0
                width  = 4
                height = 6
                content = {
                  ref = "#/spec/panels/f8d0fdea-1749-5fb2-ad7e-deb74b2aa099"
                }
              },
              {
                x      = 0
                y      = 6
                width  = 12
                height = 6
                content = {
                  ref = "#/spec/panels/a218d943-7e1b-5f62-8c90-3b4e7f82d519"
                }
              },
            ]
          }
        }
      },
      {
        grid = {
          kind = "Grid"
          spec = {
            display = {
              title = "Duration and Latency"
              collapse = {
                open = true
              }
            }
            items = [
              {
                x      = 0
                y      = 0
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/416bd861-923c-5a86-8f46-4db6a2f29308"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/94351dca-3cbd-5a86-a195-b68c51332bc8"
                }
              },
              {
                x      = 0
                y      = 6
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/e687b321-4f2a-5b8c-9c71-7d1a5e93c102"
                }
              },
              {
                x      = 6
                y      = 6
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/c517d114-b7f4-5e94-9306-68ebd42103cb"
                }
              },
            ]
          }
        }
      },
      {
        grid = {
          kind = "Grid"
          spec = {
            display = {
              title = "Traces"
              collapse = {
                open = true
              }
            }
            items = [
              {
                x      = 0
                y      = 0
                width  = 12
                height = 8
                content = {
                  ref = "#/spec/panels/867e521d-f095-52c7-9440-af4273fd8691"
                }
              },
            ]
          }
        }
      },
    ]
  }
}
