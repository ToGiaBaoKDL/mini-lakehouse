# Host dashboard: hostmetrics receiver signals (observability/signoz/collector/config.yaml).
# Metric attribute contexts follow the OTel collector hostmetrics semantics:
# host.name is a resource attribute, state/mountpoint/device are metric attributes.

resource "signoz_dashboard" "host_overview" {
  schema_version = "v6"
  name           = "lakehouse-host-overview"
  tags = [
    {
      key   = "tag"
      value = "host"
    },
    {
      key   = "tag"
      value = "metrics"
    },
    {
      key   = "tag"
      value = "dev"
    },
  ]

  spec = {
    display = {
      name        = "Infrastructure / Host"
      description = "Host CPU, memory, load, filesystem, disk and network from the hostmetrics receiver."
    }
    links = []
    variables = [
      {
        list_variable = {
          kind = "ListVariable"
          spec = {
            display = {
              name        = "host_name"
              description = "Host reporting hostmetrics"
            }
            allow_all_value = true
            allow_multiple  = true
            sort            = "alphabetical-asc"
            name            = "host_name"
            plugin = {
              dynamic_variable = {
                kind = "signoz/DynamicVariable"
                spec = {
                  name   = "host.name"
                  signal = "metrics"
                }
              }
            }
          }
        }
      },
    ]
    panels = merge({
      for id, panel in local.dashboard_metric_kpi_panels : id => panel
      if local.dashboard_metric_kpis[id].dashboard == "host"
      }, {
      "f1014990-91b4-543a-ab60-99ddd4772ef8" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "CPU utilization by state"
            description = "Active CPU utilization by non-idle state, averaged across logical CPUs per host."
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
                  unit              = "percentunit"
                  decimal_precision = "2"
                }
                chart_appearance = {
                  line_interpolation = "spline"
                  show_points        = false
                  line_style         = "solid"
                  fill_mode          = "solid"
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
                            metric_name       = "system.cpu.utilization"
                            time_aggregation  = "avg"
                            space_aggregation = "avg"
                          },
                        ]
                        filter = {
                          expression = "host.name IN $host_name AND state != 'idle'"
                        }
                        group_by = [
                          {
                            name            = "host.name"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                          {
                            name            = "state"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{host.name}} / {{state}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "avg(avg(system.cpu.utilization))"
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
      "d454583c-b503-56cc-a890-313bdc4cd55b" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Memory usage"
            description = "Host memory usage by state (hostmetrics)."
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
                  unit              = "bytes"
                  decimal_precision = "2"
                }
                chart_appearance = {
                  line_interpolation = "spline"
                  show_points        = false
                  line_style         = "solid"
                  fill_mode          = "solid"
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
                            metric_name       = "system.memory.usage"
                            time_aggregation  = "avg"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = "host.name IN $host_name"
                        }
                        group_by = [
                          {
                            name            = "host.name"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                          {
                            name            = "state"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{host.name}} / {{state}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(avg(system.memory.usage))"
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
      "91b44d33-cb13-5ba3-bd31-60dac91d0971" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Load average"
            description = "1, 5 and 15 minute load averages (hostmetrics)."
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
                                    metric_name       = "system.cpu.load_average.1m"
                                    time_aggregation  = "avg"
                                    space_aggregation = "max"
                                  },
                                ]
                                filter = {
                                  expression = "host.name IN $host_name"
                                }
                                group_by = [
                                  {
                                    name            = "host.name"
                                    field_context   = "resource"
                                    field_data_type = "string"
                                  },
                                ]
                                having = {
                                  expression = ""
                                }
                                legend = "{{host.name}} / load 1"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "max(avg(system.cpu.load_average.1m))"
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
                                    metric_name       = "system.cpu.load_average.5m"
                                    time_aggregation  = "avg"
                                    space_aggregation = "max"
                                  },
                                ]
                                filter = {
                                  expression = "host.name IN $host_name"
                                }
                                group_by = [
                                  {
                                    name            = "host.name"
                                    field_context   = "resource"
                                    field_data_type = "string"
                                  },
                                ]
                                having = {
                                  expression = ""
                                }
                                legend = "{{host.name}} / load 5"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "max(avg(system.cpu.load_average.5m))"
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
                                    metric_name       = "system.cpu.load_average.15m"
                                    time_aggregation  = "avg"
                                    space_aggregation = "max"
                                  },
                                ]
                                filter = {
                                  expression = "host.name IN $host_name"
                                }
                                group_by = [
                                  {
                                    name            = "host.name"
                                    field_context   = "resource"
                                    field_data_type = "string"
                                  },
                                ]
                                having = {
                                  expression = ""
                                }
                                legend = "{{host.name}} / load 15"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "max(avg(system.cpu.load_average.15m))"
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
      "03baacbf-1d8d-57d2-9856-75fd006fafac" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Filesystem utilization"
            description = "Filesystem utilization by mountpoint; warning, error, and critical thresholds are 70%, 80%, and 90%."
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
                  unit              = "percentunit"
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
                            metric_name       = "system.filesystem.utilization"
                            time_aggregation  = "avg"
                            space_aggregation = "max"
                          },
                        ]
                        filter = {
                          expression = "host.name IN $host_name"
                        }
                        group_by = [
                          {
                            name            = "host.name"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                          {
                            name            = "mountpoint"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{host.name}} / {{mountpoint}}"
                        limit  = 100
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
                }
              }
            },
          ]
        }
      }
      "ae42dd7f-d9d0-5b9f-afc0-889b5e48a9b0" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Disk I/O"
            description = "Disk IO rate (bytes/s) by device and direction."
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
                  unit              = "Bps"
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
                            metric_name       = "system.disk.io"
                            time_aggregation  = "rate"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = "host.name IN $host_name"
                        }
                        group_by = [
                          {
                            name            = "host.name"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                          {
                            name            = "device"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                          {
                            name            = "direction"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{host.name}} / {{device}} {{direction}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(rate(system.disk.io))"
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
      "34878f77-4459-5302-b39d-7a2586df87d9" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Network I/O"
            description = "Network receive/transmit rate (bytes/s) by device."
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
                  unit              = "Bps"
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
                            metric_name       = "system.network.io"
                            time_aggregation  = "rate"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = "host.name IN $host_name"
                        }
                        group_by = [
                          {
                            name            = "host.name"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                          {
                            name            = "device"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                          {
                            name            = "direction"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{host.name}} / {{device}} {{direction}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(rate(system.network.io))"
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
                  ref = "#/spec/panels/c0ac575a-c5be-4392-9d53-2c78f7315159"
                }
              },
              {
                x      = 3
                y      = 0
                width  = 3
                height = 3
                content = {
                  ref = "#/spec/panels/e51bca2f-9a00-4807-97c4-f2bec18670a6"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 3
                height = 3
                content = {
                  ref = "#/spec/panels/d81ef4a1-1694-48c5-b111-fa706334d883"
                }
              },
              {
                x      = 9
                y      = 0
                width  = 3
                height = 3
                content = {
                  ref = "#/spec/panels/f1017d6a-35a4-4220-9bf2-0e65a907114d"
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
              title = "Resource utilization"
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
                  ref = "#/spec/panels/f1014990-91b4-543a-ab60-99ddd4772ef8"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/d454583c-b503-56cc-a890-313bdc4cd55b"
                }
              },
              {
                x      = 0
                y      = 6
                width  = 4
                height = 6
                content = {
                  ref = "#/spec/panels/91b44d33-cb13-5ba3-bd31-60dac91d0971"
                }
              },
              {
                x      = 4
                y      = 6
                width  = 8
                height = 6
                content = {
                  ref = "#/spec/panels/03baacbf-1d8d-57d2-9856-75fd006fafac"
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
              title = "Saturation and I/O"
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
                  ref = "#/spec/panels/ae42dd7f-d9d0-5b9f-afc0-889b5e48a9b0"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/34878f77-4459-5302-b39d-7a2586df87d9"
                }
              },
            ]
          }
        }
      },
    ]
  }
}
