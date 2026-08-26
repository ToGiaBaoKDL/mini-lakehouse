# Synthetic Probing dashboard: http_check receiver signals (sysops/signoz/collector/config.yaml).
# Private origin health plus public Cloudflare edge TLS certificate probes.

resource "signoz_dashboard" "synthetic_probing" {
  schema_version = "v6"
  name           = "lakehouse-synthetic-probing"
  tags = [
    {
      key   = "tag"
      value = "synthetic"
    },
    {
      key   = "tag"
      value = "ingress"
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
      name        = "Infrastructure / Synthetic checks"
      description = "Private application health and latency, plus public Cloudflare edge TLS certificate validity."
    }
    links = []
    variables = [
      {
        list_variable = {
          kind = "ListVariable"
          spec = {
            display = {
              name        = "http_url"
              description = "Probed endpoint URL (http.url)"
            }
            allow_all_value = true
            allow_multiple  = true
            sort            = "alphabetical-asc"
            name            = "http_url"
            plugin = {
              dynamic_variable = {
                kind = "signoz/DynamicVariable"
                spec = {
                  name   = "http.url"
                  signal = "metrics"
                }
              }
            }
          }
        }
      },
    ]
    panels = {
      "a1100001-0001-4000-8000-000000000001" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Endpoint availability"
            description = "Private origin 2xx availability ratio (1.0 = healthy, 0 = non-2xx)."
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
                  soft_max     = 1
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
                            metric_name       = "httpcheck.status"
                            time_aggregation  = "avg"
                            space_aggregation = "avg"
                          },
                        ]
                        filter = {
                          expression = "http.url IN $http_url AND http.status_class = '2xx'"
                        }
                        group_by = [
                          {
                            name            = "http.url"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{http.url}}"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "avg(avg(httpcheck.status))"
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
      "a1100001-0001-4000-8000-000000000002" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "TLS certificate remaining validity"
            description = "Time remaining until SSL/TLS certificate expiration for HTTPS endpoints."
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
                  decimal_precision = "0"
                }
                chart_appearance = {
                  line_interpolation = "linear"
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
                            metric_name       = "httpcheck.tls.cert_remaining"
                            time_aggregation  = "avg"
                            space_aggregation = "min"
                          },
                        ]
                        filter = {
                          expression = "http.url IN $http_url"
                        }
                        group_by = [
                          {
                            name            = "http.url"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{http.url}} cert remaining"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "min(avg(httpcheck.tls.cert_remaining))"
                            }
                            direction = "asc"
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
      "a1100001-0001-4000-8000-000000000003" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Average response time"
            description = "Average HTTP probe response duration in milliseconds."
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
                            metric_name       = "httpcheck.duration"
                            time_aggregation  = "avg"
                            space_aggregation = "avg"
                          },
                        ]
                        filter = {
                          expression = "http.url IN $http_url"
                        }
                        group_by = [
                          {
                            name            = "http.url"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{http.url}} avg"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "avg(avg(httpcheck.duration))"
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
      "a1100001-0001-4000-8000-000000000004" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Peak response time"
            description = "Peak HTTP probe response duration (max duration in ms per interval)."
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
                            metric_name       = "httpcheck.duration"
                            time_aggregation  = "max"
                            space_aggregation = "max"
                          },
                        ]
                        filter = {
                          expression = "http.url IN $http_url"
                        }
                        group_by = [
                          {
                            name            = "http.url"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{http.url}} max"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "max(max(httpcheck.duration))"
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
      "a1100001-0001-4000-8000-000000000006" = {
        kind = "Panel"
        spec = {
          display = {
            name        = "Probe failures per interval"
            description = "Failed synthetic probe attempts in each chart interval, grouped by endpoint."
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
                      metrics = {
                        name   = "A"
                        signal = "metrics"
                        aggregations = [
                          {
                            metric_name       = "httpcheck.error"
                            time_aggregation  = "increase"
                            space_aggregation = "sum"
                          },
                        ]
                        filter = {
                          expression = "http.url IN $http_url"
                        }
                        group_by = [
                          {
                            name            = "http.url"
                            field_context   = "attribute"
                            field_data_type = "string"
                          },
                        ]
                        having = {
                          expression = ""
                        }
                        legend = "{{http.url}} failures"
                        limit  = 100
                        order = [
                          {
                            key = {
                              name = "sum(increase(httpcheck.error))"
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
              title = "Availability and TLS"
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
                  ref = "#/spec/panels/a1100001-0001-4000-8000-000000000001"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/a1100001-0001-4000-8000-000000000002"
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
              title = "Latency and response performance"
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
                  ref = "#/spec/panels/a1100001-0001-4000-8000-000000000003"
                }
              },
              {
                x      = 6
                y      = 0
                width  = 6
                height = 6
                content = {
                  ref = "#/spec/panels/a1100001-0001-4000-8000-000000000004"
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
              title = "Probe failures"
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
                  ref = "#/spec/panels/a1100001-0001-4000-8000-000000000006"
                }
              },
            ]
          }
        }
      },
    ]
  }
}
