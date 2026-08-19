# Metadata backup status dashboard: parses of the JSON audit lines written by
# infra/runtime/postgres/backup and tailed by the collection agent's
# file_log/lakehouse_audit receiver. The backup cadence is 12h with a 15h
# alert window (plans/2026-08-17-signoz-observability-and-backup-validation.md).

resource "signoz_dashboard" "metadata_backup_status" {
  schema_version = "v6"
  name           = "lakehouse-metadata-backup"
  tags = [
    {
      key   = "tag"
      value = "backup"
    },
    {
      key   = "tag"
      value = "logs"
    },
    {
      key   = "tag"
      value = "dev"
    },
  ]

  spec = {
    display = {
      name        = "Metadata Backup Status"
      description = "Audit trail of metadata Postgres backups: per-slot outcome, freshness, and failure reasons."
    }
    links     = []
    variables = []
    panels = {
      "0fa5f972-0822-578d-8702-411c35133e96" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Recent audit events"
            description = "Most recent backup audit lines (status, database, slot, bytes)."
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
                      logs = {
                        name          = "A"
                        signal        = "logs"
                        step_interval = "60"
                        filter = {
                          expression = "status EXISTS"
                        }
                        select_fields = [
                          {
                            name            = "timestamp"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                          {
                            name            = "status"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                          {
                            name            = "database"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                          {
                            name            = "slot"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                          {
                            name            = "bytes"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                          {
                            name            = "error"
                            field_context   = "attribute"
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
                          {
                            key = {
                              name = "id"
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
      "e02e38db-80b8-5e41-8217-e08fdf7f8301" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Audit outcomes by status"
            description = "Backup audit lines per status (success/failure/skipped)."
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
                  show_points        = true
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
                      logs = {
                        name          = "A"
                        signal        = "logs"
                        step_interval = "300"
                        aggregations = [
                          {
                            expression = "count()"
                          },
                        ]
                        filter = {
                          expression = "status EXISTS"
                        }
                        group_by = [
                          {
                            name            = "status"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{status}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "count()"
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
      "6e04c721-b7f5-50ff-9a3c-35f0ea2dae24" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Successful slots by database"
            description = "Success lines per database; gaps beyond one 12h cadence mean a missed slot."
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
                  line_interpolation = "linear"
                  show_points        = true
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
                      logs = {
                        name          = "A"
                        signal        = "logs"
                        step_interval = "3600"
                        aggregations = [
                          {
                            expression = "count()"
                          },
                        ]
                        filter = {
                          expression = "status = 'success'"
                        }
                        group_by = [
                          {
                            name            = "database"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{database}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "count()"
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
      "67079f05-0dbb-509c-90d8-b815638f5860" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Backup duration"
            description = "Backup duration in seconds per database."
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
                  show_points        = true
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
                      logs = {
                        name          = "A"
                        signal        = "logs"
                        step_interval = "3600"
                        aggregations = [
                          {
                            expression = "avg(duration_seconds)"
                          },
                        ]
                        filter = {
                          expression = "status = 'success'"
                        }
                        group_by = [
                          {
                            name            = "database"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{database}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "avg(duration_seconds)"
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
    }
    layouts = [
      {
        grid = {
          kind = "Grid"
          spec = {
            display = {
              title = "Metadata backup"
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
                  ref = "#/spec/panels/e02e38db-80b8-5e41-8217-e08fdf7f8301"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/6e04c721-b7f5-50ff-9a3c-35f0ea2dae24"
                }
              },
              {
                x      = 0
                y      = 6
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/67079f05-0dbb-509c-90d8-b815638f5860"
                }
              },
              {
                x      = 6
                y      = 6
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/0fa5f972-0822-578d-8702-411c35133e96"
                }
              },
            ]
          }
        }
      },
    ]
  }
}
