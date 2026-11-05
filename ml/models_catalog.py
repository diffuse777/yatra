"""Catalog metadata for the Admin Models section (explanatory pages + LSTM)."""

MODEL_CATEGORIES = {
    "Demand Forecasting": [
        {
            "short_name": "LSTM",
            "name": "LSTM",
            "full_name": "Long Short-Term Memory",
            "description": "Deep learning time-series model that learns temporal passenger demand patterns from historical transport data.",
            "purpose": "Forecast future passenger demand over time.",
            "endpoint": "admin_models_lstm",
        },
        {
            "short_name": "XGBoost",
            "name": "XGBoost",
            "full_name": "Extreme Gradient Boosting",
            "description": "Gradient-boosted trees for structured demand features with interpretable feature importance.",
            "purpose": "Predict demand from engineered tabular transport features.",
            "endpoint": "admin_models_xgboost",
        },
        {
            "short_name": "Hybrid",
            "name": "Hybrid LSTM + XGBoost",
            "full_name": "Hybrid LSTM and XGBoost Ensemble",
            "description": "Combines temporal sequence learning with feature-based boosting for stronger demand forecasts.",
            "purpose": "Fuse sequence and tabular signals into a final demand forecast.",
            "endpoint": "admin_models_hybrid",
        },
    ],
    "Route Optimization": [
        {
            "short_name": "NSGA-II",
            "name": "NSGA-II",
            "full_name": "Non-dominated Sorting Genetic Algorithm II",
            "description": "Multi-objective evolutionary optimizer for competing transport goals such as time, cost, and crowd.",
            "purpose": "Generate Pareto-optimal route and service trade-offs.",
            "endpoint": "admin_models_nsga2",
        },
    ],
    "Network Intelligence": [
        {
            "short_name": "GNN",
            "name": "Graph Neural Network",
            "full_name": "Graph Neural Network",
            "description": "Learns from the transport network graph — stops as nodes and services as edges — for network-level prediction.",
            "purpose": "Predict network flows, delays, or connectivity effects.",
            "endpoint": "admin_models_gnn",
        },
    ],
    "Spatial Intelligence": [
        {
            "short_name": "GIS",
            "name": "GIS Spatial Analytics",
            "full_name": "Geographic Information System Spatial Analytics",
            "description": "Spatial analysis of accessibility, coverage, and geographic demand or crowd hotspots.",
            "purpose": "Map and quantify spatial transport intelligence.",
            "endpoint": "admin_models_gis",
        },
    ],
    "Personalization": [
        {
            "short_name": "RecSys",
            "name": "Hybrid Recommendation System",
            "full_name": "Hybrid Travel Recommendation System",
            "description": "Combines content-based, collaborative, and context-aware signals for personalized travel suggestions.",
            "purpose": "Recommend routes, times, and services to passengers.",
            "endpoint": "admin_models_recommendation",
        },
    ],
}


MODEL_DETAILS = {
    "lstm": {
        "short_name": "LSTM",
        "name": "LSTM Demand Forecasting",
        "full_name": "Long Short-Term Memory",
        "category": "Deep Learning / Time-Series Forecasting",
        "purpose": "Forecast passenger demand over future time periods.",
        "problem": "Passenger Demand Forecasting — anticipating how many passengers will travel in upcoming hours/days.",
        "algorithm": "Stacked LSTM recurrent neural network (PyTorch).",
        "approach": "Supervised sequence learning on chronologically ordered hourly demand windows with EarlyStopping.",
        "input": "Historical transport demand / time-series features (hourly).",
        "output": "Future passenger demand (next-step and multi-hour forecast).",
        "main_features": "passenger_demand, hour, day_of_week, month, is_holiday, weather_code (when available).",
        "why": (
            "Passenger demand is sequential and seasonal. LSTM memory cells capture peak-hour, weekday/weekend, "
            "and holiday patterns that matter for Tamil Nadu corridor planning in Yatra Saarthi."
        ),
        "data": {
            "dataset_name": "Operational hourly demand (StopAnalytics / Bookings / bus schedule occupancy)",
            "dataset_size": "Resolved at training time from project data",
            "features": "passenger_demand, hour, day_of_week, month, is_holiday, weather_code",
            "target": "passenger_demand",
            "data_range": "Resolved at training time",
            "preprocessing": "Operational source → validate → fill gaps → time sort → scale → sequences → chronological split",
        },
        "implementation": {
            "architecture": "Input → LSTM → Dropout → LSTM → Dense → Demand Prediction",
            "parameters": "sequence_length, lstm_units, dropout, batch_size, epochs, learning_rate",
            "config": "EarlyStopping on val_loss; shuffle=False; artifacts saved under ml_results/lstm/",
        },
        "insights": [
            "LSTM is suited to temporal passenger-demand patterns with recurring peaks.",
            "Forecasts can feed AI Crowd Intelligence and timetable capacity planning.",
            "Train explicitly from this page; results persist so the page never auto-retrains on load.",
        ],
        "template": "models/lstm.html",
    },
    "xgboost": {
        "short_name": "XGBoost",
        "name": "XGBoost Demand Forecasting",
        "full_name": "Extreme Gradient Boosting",
        "category": "Machine Learning / Gradient Boosted Decision Trees",
        "purpose": "Predict passenger demand using structured transport, temporal, and operational features.",
        "problem": "Passenger Demand Forecasting — structured tabular prediction with interpretable feature importance.",
        "algorithm": "Gradient boosting decision trees (XGBoost XGBRegressor).",
        "approach": "Boosted tree ensembles on engineered demand features; gain-based feature importance; chronological train/test split.",
        "input": "Structured features: hour, day_of_week, month, weekend/holiday, origin, destination, operator, bus_type, fare, duration, seats, lag demand.",
        "output": "Predicted passenger demand (occupied seats per scheduled trip occurrence).",
        "main_features": "Temporal calendars, route endpoints, operator/bus type, fare/duration, past-only lag/rolling demand.",
        "why": (
            "XGBoost handles mixed categorical/numeric transport features well and surfaces which signals "
            "drive demand — useful for operations and admin analytics in Yatra Saarthi."
        ),
        "data": {
            "dataset_name": "BusSchedule_structured_trip_demand (operational occupied seats)",
            "dataset_size": "Resolved at training time",
            "features": "hour, day_of_week, month, is_weekend, is_holiday, origin, destination, operator, bus_type, service_kind, fare, duration_minutes, total_seats, lag_1, lag_24, rolling_mean_3",
            "target": "passenger_demand",
            "data_range": "Resolved at training time",
            "preprocessing": "Schedule expand → lag/rolling (past-only) → one-hot categoricals (train-fit) → chronological 80/20 split",
        },
        "implementation": {
            "architecture": "XGBRegressor (reg:squarederror) over one-hot + numeric feature matrix",
            "parameters": "n_estimators, max_depth, learning_rate, subsample, colsample_bytree, min_child_weight, reg_alpha, reg_lambda",
            "config": "Early stopping on chronological validation slice; artifacts under ml_results/xgboost/",
        },
        "insights": [
            "Feature importance shows which operational levers most influence demand.",
            "Train explicitly from this page; results persist so the page never auto-retrains on load.",
            "Independent from LSTM — hybrid fusion is not implemented here.",
        ],
        "template": "models/xgboost.html",
    },
    "hybrid": {
        "short_name": "Hybrid",
        "name": "Hybrid LSTM + XGBoost",
        "full_name": "Hybrid LSTM + XGBoost Demand Forecasting",
        "category": "Ensemble / Hybrid Forecasting",
        "purpose": (
            "Combine temporal sequence learning and structured feature learning "
            "for passenger demand forecasting."
        ),
        "problem": "Passenger Demand Forecasting",
        "algorithm": "Stacking: LSTM + XGBoost → Ridge/Linear meta-model → hybrid demand",
        "approach": "LSTM + XGBoost + Hybrid Meta-Model (stacking / meta-learning)",
        "input": (
            "LSTM: sliding hourly demand sequences; XGBoost: calendar + past-only lag features; "
            "Meta: base predictions + hour / day_of_week / is_holiday"
        ),
        "output": "Final hybrid passenger demand prediction per aligned timestamp",
        "main_features": "Aligned hourly operational demand; LSTM preds + XGBoost preds + temporal context",
        "why": (
            "Yatra Saarthi demand has both strong time-series seasonality and structured calendar "
            "patterns. Stacking lets a meta-model learn when to trust each branch."
        ),
        "data": {
            "dataset_name": "Aligned hourly operational demand (same family as LSTM)",
            "dataset_size": "—",
            "features": "LSTM sequence features + XGBoost tabular lags / calendar fields",
            "target": "passenger_demand",
            "data_range": "—",
            "preprocessing": (
                "Chronological 70/15/15 train/val/test; scaler fit on train only; "
                "meta-model fit on validation predictions only (no test leakage)"
            ),
        },
        "implementation": {
            "architecture": "Historical Demand → LSTM & XGBoost → Meta-Model → Hybrid Prediction",
            "parameters": "LSTM units/dropout + XGBoost tree params + Ridge alpha / meta features",
            "config": "Identical chronological partitions for both branches; stacking on validation",
        },
        "insights": [
            "Train explicitly from this page; results persist without auto-retrain on load.",
            "Comparison metrics are computed on the same held-out test rows for all three models.",
            "Hybrid is not claimed better unless test metrics show it.",
        ],
        "template": "models/hybrid.html",
    },
    "nsga2": {
        "short_name": "NSGA-II",
        "name": "NSGA-II Route Optimization",
        "full_name": "Non-dominated Sorting Genetic Algorithm II",
        "category": "Multi-Objective Optimization",
        "purpose": "Optimize transport plans across competing objectives.",
        "problem": "Route and service choices must trade off travel time, cost, distance, transfers, and crowd.",
        "algorithm": "NSGA-II multi-objective evolutionary algorithm.",
        "approach": "Population-based search with non-dominated sorting and crowding distance for Pareto fronts.",
        "input": "Candidate routes/services with objective evaluators (time, cost, distance, transfers, crowd, etc.).",
        "output": "Pareto-optimal solution set for operator / passenger decision support.",
        "main_features": "Travel time, cost, distance, transfers, crowd exposure, other valid transport objectives.",
        "why": (
            "Yatra Saarthi multi-modal coordination needs transparent trade-offs, not a single blended score."
        ),
        "data": {
            "dataset_name": "Route / service objective evaluation set",
            "dataset_size": "—",
            "features": "Objective vectors per candidate solution",
            "target": "Pareto front (multi-objective; no single target)",
            "data_range": "—",
            "preprocessing": "Normalize objectives; define feasible operators and constraints",
        },
        "implementation": {
            "architecture": "Chromosome encoding of route choices → fitness vector → NSGA-II selection",
            "parameters": "population_size, generations, crossover, mutation, objective weights (optional)",
            "config": "Constraint handling for valid transfers and schedule windows",
        },
        "insights": [
            "Operators can pick from Pareto solutions that fit policy priorities.",
            "Crowd-aware objectives connect optimization to demand forecasts.",
        ],
        "template": "models/model_detail.html",
    },
    "gnn": {
        "short_name": "GNN",
        "name": "Graph Neural Network",
        "full_name": "Graph Neural Network",
        "category": "Graph Representation Learning",
        "purpose": "Learn predictions on the transport network graph.",
        "problem": "Stops and services form a graph; local models ignore network structure.",
        "algorithm": "Message-passing graph neural network over the transport graph.",
        "approach": "Nodes exchange neighborhood information; edges carry service/relation attributes.",
        "input": "Transport network as a graph with node features and edge features.",
        "output": "Network-level predictions (e.g., flow, congestion risk, link importance).",
        "main_features": "Nodes (stops/hubs), edges (services/connections), node features, edge features.",
        "why": (
            "Tamil Nadu corridor networks are interconnected; GNN captures spillover between hubs "
            "such as Madurai, Virudhunagar, and Krishnankovil."
        ),
        "data": {
            "dataset_name": "Transport graph derived from routes, stops, and services",
            "dataset_size": "—",
            "features": "Node attributes (demand, degree, zone) and edge attributes (frequency, distance, fare)",
            "target": "Task-specific network label / regression target",
            "data_range": "—",
            "preprocessing": "Graph construction, feature normalization, train/val edge or node splits",
        },
        "implementation": {
            "architecture": "GCN / GAT / GraphSAGE-style layers with task head",
            "parameters": "hidden_dim, layers, dropout, learning_rate",
            "config": "Neighborhood sampling / full-batch depending on graph size",
        },
        "insights": [
            "Network predictions support disruption impact analysis.",
            "Pairs naturally with GIS for spatial-network views.",
        ],
        "template": "models/model_detail.html",
    },
    "gis": {
        "short_name": "GIS",
        "name": "GIS Spatial Analytics",
        "full_name": "Geographic Information System Spatial Analytics",
        "category": "Spatial Intelligence",
        "purpose": "Analyze geographic transport accessibility and hotspots.",
        "problem": "Demand and crowd vary spatially; schedules alone do not show coverage gaps.",
        "algorithm": "GIS spatial analytics (buffering, hotspot detection, accessibility surfaces).",
        "approach": "Overlay stops, routes, demand, and crowd layers for spatial metrics and maps.",
        "input": "Geographic coordinates of stops/routes plus demand and crowd attributes.",
        "output": "Accessibility scores, demand hotspots, crowd hotspots, coverage diagnostics.",
        "main_features": "Geographic data, spatial joins, transport accessibility, hotspot indicators.",
        "why": (
            "Yatra Saarthi already stores stop coordinates and corridor geography; GIS turns that into "
            "coverage and hotspot intelligence for planners."
        ),
        "data": {
            "dataset_name": "Geo-enabled stops, routes, demand, and crowd layers",
            "dataset_size": "—",
            "features": "lat/lon, route geometries, demand/crowd intensities",
            "target": "Spatial metrics / hotspot classifications",
            "data_range": "—",
            "preprocessing": "CRS alignment, spatial indexing, outlier coordinate checks",
        },
        "implementation": {
            "architecture": "Spatial pipelines (e.g., GeoPandas / PostGIS-style analytics) + map layers",
            "parameters": "buffer radii, hotspot thresholds, accessibility decay",
            "config": "Map exports and tabular hotspot summaries for admin views",
        },
        "insights": [
            "Hotspots can trigger service densification or crowd alerts.",
            "Accessibility gaps highlight underserved areas along corridors.",
        ],
        "template": "models/model_detail.html",
    },
    "recommendation": {
        "short_name": "RecSys",
        "name": "Hybrid Recommendation System",
        "full_name": "Hybrid Travel Recommendation System",
        "category": "Personalization / Recommender Systems",
        "purpose": "Deliver personalized travel recommendations to passengers.",
        "problem": "Passengers need context-aware suggestions for routes, times, and modes.",
        "algorithm": "Hybrid recommender: content-based + collaborative filtering + context-aware ranking.",
        "approach": (
            "Content-based matching on trip attributes; collaborative signals from similar travelers; "
            "context (time, weather, crowd) for final ranking."
        ),
        "input": "Passenger preferences, historical choices, trip catalog, and live context features.",
        "output": "Ranked personalized travel recommendations.",
        "main_features": "User/item profiles, interaction history, time/weather/crowd context.",
        "why": (
            "Yatra Saarthi already models passenger preferences and recommendations; a hybrid system "
            "makes those suggestions more accurate and context-aware."
        ),
        "data": {
            "dataset_name": "PassengerPreference / recommendation interaction logs + trip catalog",
            "dataset_size": "—",
            "features": "User features, item features, contextual features",
            "target": "Recommendation relevance / click or booking proxy",
            "data_range": "—",
            "preprocessing": "Cold-start handling, privacy-aware aggregation, train/test by time",
        },
        "implementation": {
            "architecture": "Hybrid ranker combining content, collaborative, and context scores",
            "parameters": "embedding dims, neighbor counts, context weights",
            "config": "Offline evaluation with ranking metrics when interaction data is sufficient",
        },
        "insights": [
            "Recommendations can surface quieter departure times from demand forecasts.",
            "Context-aware ranking aligns with weather and crowd modules.",
        ],
        "template": "models/model_detail.html",
    },
}
