# Metadata PostgreSQL dashboard: postgresql receiver signals from the
# lakehouse_monitor role (sysops/signoz/collector/config.yaml). Only the
# receiver's default-enabled metrics are queried; postgresql.database.name carries the
# database name.

resource "signoz_dashboard" "metadata_postgres" {
  schema_version = "v6"
  name           = "lakehouse-metadata-postgres"
  tags = [
    {
      key   = "tag"
      value = "postgres"
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
      name        = "Platform / PostgreSQL"
      description = "Connection, transaction, table size, and row-activity metrics for the shared metadata server."
    }
    links = []
    variables = [
      {
        list_variable = {
          kind = "ListVariable"
          spec = {
            display = {
              name        = "database"
              description = "PostgreSQL database"
            }
            allow_all_value = true
            allow_multiple  = true
            sort            = "alphabetical-asc"
            name            = "database"
            plugin = {
              dynamic_variable = {
                kind = "signoz/DynamicVariable"
                spec = {
                  name   = "postgresql.database.name"
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
      if local.dashboard_metric_kpis[id].dashboard == "postgres"
      }, {
      "be7e7574-01d4-5e62-9670-2ee117ed1131" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Database backends"
            description = "Backend processes per database."
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
                            metric_name       = "postgresql.backends"
                            time_aggregation  = "avg"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = "postgresql.database.name IN $database"
                        }
                        group_by = [
                          {
                            name            = "postgresql.database.name"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{postgresql.database.name}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(avg(postgresql.backends))"
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
      "6b92defb-5066-5a26-a8d4-b22f10058b7d" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Transaction rate"
            description = "Commits and rollbacks per second per database."
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
                                    metric_name       = "postgresql.commits"
                                    time_aggregation  = "rate"
                                    space_aggregation = "sum"
                                  },
                                ]
                                filter = {
                                  expression = "postgresql.database.name IN $database"
                                }
                                group_by = [
                                  {
                                    name            = "postgresql.database.name"
                                    field_context   = "resource"
                                    field_data_type = "string"
                                  },
                                ]
                                having = {
                                  expression = ""
                                }
                                legend = "{{postgresql.database.name}} commits"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "sum(rate(postgresql.commits))"
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
                                    metric_name       = "postgresql.rollbacks"
                                    time_aggregation  = "rate"
                                    space_aggregation = "sum"
                                  },
                                ]
                                filter = {
                                  expression = "postgresql.database.name IN $database"
                                }
                                group_by = [
                                  {
                                    name            = "postgresql.database.name"
                                    field_context   = "resource"
                                    field_data_type = "string"
                                  },
                                ]
                                having = {
                                  expression = ""
                                }
                                legend = "{{postgresql.database.name}} rollbacks"
                                limit  = 100
                                order = [
                                  {
                                    key = {
                                      name = "sum(rate(postgresql.rollbacks))"
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
      "de99ee73-30f8-57b5-9ed7-6455b4ea03f9" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Database size"
            description = "On-disk database size."
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
                            metric_name       = "postgresql.db_size"
                            time_aggregation  = "avg"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = "postgresql.database.name IN $database"
                        }
                        group_by = [
                          {
                            name            = "postgresql.database.name"
                            field_context   = "resource"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{postgresql.database.name}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(avg(postgresql.db_size))"
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
      "195bf111-9ef9-5aef-81f5-0efab343823d" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Row operations"
            description = "Insert, update, delete, and hot-update rates by operation."
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
                            metric_name       = "postgresql.operations"
                            time_aggregation  = "rate"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = "postgresql.database.name IN $database"
                        }
                        group_by = [
                          {
                            name            = "operation"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{operation}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(rate(postgresql.operations))"
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
      "e9966052-241d-58d6-a273-ae2749c0c0d0" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Live and dead rows"
            description = "Row counts by state; a growing dead-tuple count signals vacuum lag."
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
                            metric_name       = "postgresql.rows"
                            time_aggregation  = "avg"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = "postgresql.database.name IN $database"
                        }
                        group_by = [
                          {
                            name            = "state"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{state}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(avg(postgresql.rows))"
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
                  ref = "#/spec/panels/6f5a4a96-9362-43a5-b833-2908dbc8b9a5"
                }
              },
              {
                x      = 3
                y      = 0
                width  = 3
                height = 3
                content = {
                  ref = "#/spec/panels/630b6a99-cd80-428e-b9ea-69fce3db5e7b"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 3
                height = 3
                content = {
                  ref = "#/spec/panels/59de448c-857c-48eb-93ab-688134e834c8"
                }
              },
              {
                x      = 9
                y      = 0
                width  = 3
                height = 3
                content = {
                  ref = "#/spec/panels/e96ec35d-c025-4c5b-9d5a-1b38f55add54"
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
              title = "Activity and storage"
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
                  ref = "#/spec/panels/be7e7574-01d4-5e62-9670-2ee117ed1131"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/6b92defb-5066-5a26-a8d4-b22f10058b7d"
                }
              },
              {
                x      = 0
                y      = 6
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/de99ee73-30f8-57b5-9ed7-6455b4ea03f9"
                }
              },
              {
                x      = 6
                y      = 6
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/195bf111-9ef9-5aef-81f5-0efab343823d"
                }
              },
              {
                x      = 0
                y      = 12
                width  = 12
                height = 6
                content = {
                  ref = "#/spec/panels/e9966052-241d-58d6-a273-ae2749c0c0d0"
                }
              },
            ]
          }
        }
      },
    ]
  }
}
