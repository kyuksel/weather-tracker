# Architecture

## System diagram

```mermaid
graph TD
    subgraph External
        WG["weather.gov API"]
    end

    subgraph Application
        FA["FastAPI app\n(uvicorn)"]
        SC["APScheduler\n(AsyncIOScheduler)"]
        PL["Poller"]
        WC["WeatherGovClient\n(httpx + tenacity)"]
        RP["Repositories"]
        ORM["SQLAlchemy ORM"]
    end

    subgraph Storage
        DB[("SQLite\n(Docker named volume)")]
    end

    subgraph Client
        AC["API client\n(curl / browser)"]
    end

    SC -->|"triggers on interval"| PL
    PL -->|"fetch forecast"| WC
    WC -->|"GET /points & /forecast/hourly"| WG
    WG -->|"hourly forecast JSON"| WC
    WC -->|"parsed observations"| PL
    PL -->|"bulk insert"| RP
    RP -->|"INSERT rows"| ORM
    ORM -->|"write"| DB

    AC -->|"GET /forecasts/extremes"| FA
    FA -->|"MIN/MAX query"| RP
    RP -->|"SELECT"| ORM
    ORM -->|"read"| DB
    DB -->|"rows"| ORM
    ORM -->|"results"| RP
    RP -->|"aggregated result"| FA
    FA -->|"JSON response"| AC

    classDef external  fill:#f4a261,stroke:#e76f51,color:#000
    classDef app       fill:#457b9d,stroke:#1d3557,color:#fff
    classDef storage   fill:#2a9d8f,stroke:#264653,color:#fff
    classDef client    fill:#8ecae6,stroke:#457b9d,color:#000

    class WG external
    class FA,SC,PL,WC,RP,ORM app
    class DB storage
    class AC client
```

## Data model relationships

The data model consists of two tables. A `location` row represents a geographic
point (latitude, longitude) and is created the first time the poller runs for a
configured coordinate pair. Each `forecast_observation` row belongs to exactly
one `location` via a foreign key (`location_id`) and records a single hourly
forecast entry captured during one polling tick: when the poll ran
(`retrieved_at`), which future hour the forecast targets (`forecast_for`), and
the predicted temperature with its unit. Because each tick appends a new row for
every target hour—rather than updating existing rows—multiple
`forecast_observation` rows share the same `(location_id, forecast_for)` pair,
capturing how the forecast evolved across successive polls. The composite index
on `(location_id, forecast_for)` makes the MIN/MAX aggregation query efficient
for any combination of location and target hour.
