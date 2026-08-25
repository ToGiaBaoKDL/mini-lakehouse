# Airflow dashboard: Airflow's native OpenTelemetry metrics (prefix "airflow",
# automation/airflow/deploy/compose.yaml: AIRFLOW__METRICS__OTEL_ON) plus its traces.
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
      value = "automation"
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
      name        = "Platform / Airflow"
      description = "Scheduler loop latency, DAG and task durations, executor capacity, schedule delay, and recent Airflow traces."
    }
    links     = []
    variables = []
    panels = merge({
      for id, panel in local.dashboard_metric_kpi_panels : id => panel
      if local.dashboard_metric_kpis[id].dashboard == "airflow"
      }, {
      "d7862908-c2b0-5858-a228-a22c1d7e78c1" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Scheduler loop duration p95"
            description = "95th-percentile scheduler loop duration in milliseconds; missing data is covered by the scheduler metrics alert."
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
                  builder_query = {
                    kind = "signoz/BuilderQuery"
                    spec = {
                      metrics = {
                        name   = "A"
                        signal = "metrics"
                        aggregations = [
                          {
                            metric_name       = "airflow.scheduler.scheduler_loop_duration.bucket"
                            time_aggregation  = ""
                            space_aggregation = "p95"
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
                        legend = "{{service.name}} p95"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "p95(airflow.scheduler.scheduler_loop_duration.bucket)"
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
      "416bd861-923c-5a86-8f46-4db6a2f29308" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Task duration p50/p95"
            description = "Task duration quantiles in milliseconds, grouped by DAG and task."
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
                                    metric_name       = "airflow.task.duration.bucket"
                                    time_aggregation  = ""
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
                                      name = "p50(airflow.task.duration.bucket)"
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
                                    metric_name       = "airflow.task.duration.bucket"
                                    time_aggregation  = ""
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
                                      name = "p95(airflow.task.duration.bucket)"
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
            name        = "Successful DAG run duration p95"
            description = "95th-percentile successful DAG run duration in milliseconds, grouped by dag_id."
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
                  builder_query = {
                    kind = "signoz/BuilderQuery"
                    spec = {
                      metrics = {
                        name   = "A"
                        signal = "metrics"
                        aggregations = [
                          {
                            metric_name       = "airflow.dagrun.duration.success.bucket"
                            time_aggregation  = ""
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
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{dag_id}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "p95(airflow.dagrun.duration.success.bucket)"
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
            name        = "Failed DAG run duration p95"
            description = "95th-percentile failed DAG run duration in milliseconds, grouped by dag_id."
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
                  builder_query = {
                    kind = "signoz/BuilderQuery"
                    spec = {
                      metrics = {
                        name   = "A"
                        signal = "metrics"
                        aggregations = [
                          {
                            metric_name       = "airflow.dagrun.duration.failed.bucket"
                            time_aggregation  = ""
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
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{dag_id}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "p95(airflow.dagrun.duration.failed.bucket)"
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
            name        = "Active DAG runs"
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
            name        = "DAG schedule delay p95"
            description = "95th-percentile delay between scheduled execution time and actual start time in milliseconds, grouped by dag_id."
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
                  builder_query = {
                    kind = "signoz/BuilderQuery"
                    spec = {
                      metrics = {
                        name   = "A"
                        signal = "metrics"
                        aggregations = [
                          {
                            metric_name       = "airflow.dagrun.schedule_delay.bucket"
                            time_aggregation  = ""
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
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{dag_id}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "p95(airflow.dagrun.schedule_delay.bucket)"
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
    })
    layouts = [
      {
        grid = {
          kind = "Grid"
          spec = {
            display = {
              title = "Key indicators"
              collapse = {
                open = true
              }
            }
            items = [
              {
                x      = 0
                y      = 0
                width  = 3
                height = 3
                content = {
                  ref = "#/spec/panels/0fe443a4-a61d-442b-b5cf-b05ee3f094da"
                }
              },
              {
                x      = 3
                y      = 0
                width  = 3
                height = 3
                content = {
                  ref = "#/spec/panels/eb5f72d8-b12c-48e7-b7ce-22f776845ed5"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 3
                height = 3
                content = {
                  ref = "#/spec/panels/8c225e28-555b-45af-b293-c14686f0991f"
                }
              },
              {
                x      = 9
                y      = 0
                width  = 3
                height = 3
                content = {
                  ref = "#/spec/panels/a95a470f-6179-4323-b66e-d302d0274ef7"
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
              title = "Concurrency and capacity"
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
                  ref = "#/spec/panels/d7862908-c2b0-5858-a228-a22c1d7e78c1"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 6
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
              title = "Duration and latency"
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
