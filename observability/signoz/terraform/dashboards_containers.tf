# Containers dashboard: docker_stats receiver signals. Compose project/service
# labels are promoted to metric attributes in the collector config
# (lakehouse.compose.project / lakehouse.compose.service), so container workloads
# are grouped by long-lived compose service.

resource "signoz_dashboard" "containers_overview" {
  schema_version = "v6"
  name           = "lakehouse-containers-overview"
  tags = [
    {
      key   = "tag"
      value = "containers"
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
      name        = "Infrastructure / Containers"
      description = "Per-container CPU, memory, and network from the docker_stats receiver, grouped by compose service."
    }
    links = []
    variables = [
      {
        list_variable = {
          kind = "ListVariable"
          spec = {
            display = {
              name        = "compose_service"
              description = "Compose service label (lakehouse.compose.service)"
            }
            allow_all_value = true
            allow_multiple  = true
            sort            = "alphabetical-asc"
            name            = "compose_service"
            plugin = {
              dynamic_variable = {
                kind = "signoz/DynamicVariable"
                spec = {
                  name   = "lakehouse.compose.service"
                  signal = "metrics"
                }
              }
            }
          }
        }
      },
      {
        list_variable = {
          kind = "ListVariable"
          spec = {
            display = {
              name        = "container_name"
              description = "Docker container name"
            }
            allow_all_value = true
            allow_multiple  = true
            sort            = "alphabetical-asc"
            name            = "container_name"
            plugin = {
              dynamic_variable = {
                kind = "signoz/DynamicVariable"
                spec = {
                  name   = "container.name"
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
      if local.dashboard_metric_kpis[id].dashboard == "containers"
      }, {
      "9ab29977-a7de-5b60-a645-cb1fc7c41a3d" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "CPU cores by compose service"
            description = "CPU cores used across containers in each Compose service; a value of 2 means two logical cores."
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
                            metric_name       = "container.cpu.utilization"
                            time_aggregation  = "avg"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = "lakehouse.compose.service IN $compose_service"
                        }
                        group_by = [
                          {
                            name            = "lakehouse.compose.service"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{lakehouse.compose.service}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(avg(container.cpu.utilization))"
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
      "e36df01f-646a-57a5-879e-d51f34bddc6e" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Memory usage by compose service"
            description = "Sum of total container memory usage per compose service."
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
                            metric_name       = "container.memory.usage.total"
                            time_aggregation  = "avg"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = "lakehouse.compose.service IN $compose_service"
                        }
                        group_by = [
                          {
                            name            = "lakehouse.compose.service"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{lakehouse.compose.service}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(avg(container.memory.usage.total))"
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
      "8b9e61ed-f042-5a40-84d3-6d27687bd5ff" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "CPU cores by container"
            description = "CPU cores used by each container; a value of 1 means one logical core."
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
                            metric_name       = "container.cpu.utilization"
                            time_aggregation  = "avg"
                            space_aggregation = "avg"
                          },
                        ]
                        filter = {
                          expression = "container.name IN $container_name"
                        }
                        group_by = [
                          {
                            name            = "container.name"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{container.name}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "avg(avg(container.cpu.utilization))"
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
      "60971be8-f21e-5686-87eb-65ae93e03e95" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Memory usage by container"
            description = "Per-container total memory usage."
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
                            metric_name       = "container.memory.usage.total"
                            time_aggregation  = "avg"
                            space_aggregation = "avg"
                          },
                        ]
                        filter = {
                          expression = "container.name IN $container_name"
                        }
                        group_by = [
                          {
                            name            = "container.name"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{container.name}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "avg(avg(container.memory.usage.total))"
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
      "c897072a-f9e5-5e47-9f34-b39c25686218" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Network I/O by container"
            description = "Receive/transmit byte rate per container."
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
                                    metric_name       = "container.network.io.usage.rx_bytes"
                                    time_aggregation  = "rate"
                                    space_aggregation = "sum"
                                  },
                                ]
                                filter = {
                                  expression = "container.name IN $container_name"
                                }
                                group_by = [
                                  {
                                    name            = "container.name"
                                    field_context   = "resource"
                                    field_data_type = "string"
                                  },
                                ]
                                having = {
                                  expression = ""
                                }
                                legend = "{{container.name}} rx"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "sum(rate(container.network.io.usage.rx_bytes))"
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
                                    metric_name       = "container.network.io.usage.tx_bytes"
                                    time_aggregation  = "rate"
                                    space_aggregation = "sum"
                                  },
                                ]
                                filter = {
                                  expression = "container.name IN $container_name"
                                }
                                group_by = [
                                  {
                                    name            = "container.name"
                                    field_context   = "resource"
                                    field_data_type = "string"
                                  },
                                ]
                                having = {
                                  expression = ""
                                }
                                legend = "{{container.name}} tx"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "sum(rate(container.network.io.usage.tx_bytes))"
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
                width  = 4
                height = 3
                content = {
                  ref = "#/spec/panels/a08e8dc8-efde-4dbb-a237-c484adf6cce5"
                }
              },
              {
                x      = 4
                y      = 0
                width  = 4
                height = 3
                content = {
                  ref = "#/spec/panels/2940fec6-f705-41d4-b65b-9631144f6f26"
                }
              },
              {
                x      = 8
                y      = 0
                width  = 4
                height = 3
                content = {
                  ref = "#/spec/panels/61ee482c-72cd-427f-82c8-9a81a3fe4df9"
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
              title = "Compose services"
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
                  ref = "#/spec/panels/9ab29977-a7de-5b60-a645-cb1fc7c41a3d"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/e36df01f-646a-57a5-879e-d51f34bddc6e"
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
              title = "Per container"
              collapse = {
                open = true
              }
            }
            items = [
              {
                x      = 0
                y      = 0
                width  = 12
                height = 6
                content = {
                  ref = "#/spec/panels/8b9e61ed-f042-5a40-84d3-6d27687bd5ff"
                }
              },
              {
                x      = 0
                y      = 6
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/60971be8-f21e-5686-87eb-65ae93e03e95"
                }
              },
              {
                x      = 6
                y      = 6
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/c897072a-f9e5-5e47-9f34-b39c25686218"
                }
              },
            ]
          }
        }
      },
    ]
  }
}
