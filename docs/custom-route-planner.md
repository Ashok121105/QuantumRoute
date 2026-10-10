# Custom Route Planner

The **Custom Route Planner** is a point-to-point road-route comparison tool. It
is deliberately separate from the multi-delivery classical/QUBO/QAOA workflow
and from Demo Mode.

## Search and route comparison

1. Select **Custom Route Planner** in the workspace sidebar.
2. Search for any supported place/address for the origin and destination, or
   choose **Use current location** and click **Use My Current Location**.
   Browser permission is requested only after that click.
   If permission is denied, manual place search remains available.
3. Check the displayed origin and destination. Either can be cleared and
   searched again.
4. Optionally upload a company fleet file. Review the parsed preview and
   validation results, then explicitly accept a valid upload for this session.
   Select its company/fleet profile and an available vehicle before choosing an
   objective. Basic point-to-point route comparison does not require fleet data.
5. Click **Compare OSRM road alternatives**. This explicit action sends the
   selected coordinates to the public OSRM routing service. Only routes
   actually returned by OSRM are displayed; the app does not imply that these
   are every physically possible route.
6. Compare route distance and OSRM estimated duration. Choose another route in
   **Selected road alternative** if desired. The selected route is drawn solid
   and thicker; other returned alternatives are dashed.
7. Use **Map view** to keep the default **2D (Folium)** map or choose
   **Tilted 3D (flat-map perspective)**. The tilted view uses the selected
   coordinates and actual OSRM route geometry, with pan, zoom, pitch, and
   rotation controls. It is a camera-tilted flat map, not a 3D city: the app
   does not invent building heights, terrain, or elevation. Its dark CARTO
   basemap needs an internet connection, uses no API token, and is subject to
   CARTO's service usage policy. The 2D OpenStreetMap view remains available as
   the fallback.

Place search uses OpenStreetMap Nominatim with an identifiable QuantumRoute
User-Agent, a one-hour in-process cache, a five-result limit, and a minimum
1.1-second interval between uncached requests. Empty searches make no request.
Public geocoding/routing endpoints receive search text/coordinates only when
the corresponding user action is submitted. The service can rate-limit or
refuse requests; those outcomes are shown without inventing a result.

## Recommendation calculation

The planner scores the distinct alternatives returned by OSRM—not all road
routes and not QAOA samples. Each available metric is min-max normalized across
the returned alternatives, with lower scores preferred:

- **Shortest Distance:** distance only.
- **Fastest Estimated Time:** OSRM duration, raised only when a supplied maximum-speed constraint requires a longer vehicle-adjusted estimate.
- **Lowest Estimated Cost:** enabled only when the selected vehicle provides
  fuel type, fuel consumption per km, fuel price per unit, and driver cost per
  hour. Estimated operating cost is fuel cost plus time-based driver cost, in
  the uploaded cost units/currency (no currency conversion is applied).
- **Green / Lower Estimated Emissions:** enabled only with fuel type and fuel
  consumption. Emissions use the application's fuel factors and are
  tailpipe-only; electric is therefore shown as zero tailpipe CO2, not zero
  lifecycle emissions.
- **Balanced:** equal weights for distance and time without vehicle metrics;
  with cost and emissions available, equal weights for distance, time,
  estimated operating cost, and tailpipe emissions. With only one of cost or
  emissions available, the available dimensions are reweighted to sum to one.

When a dimension has identical values for every returned route, it contributes
equally (zero normalized difference). A tie follows OSRM's returned order and
is not evidence that one route is uniquely optimal. Cost/emissions are shown as
unavailable unless all required inputs are present. OSRM time is an estimate,
not live traffic; estimates do not account for confirmed congestion, tolls,
road restrictions, or vehicle-specific road restrictions.
When an uploaded maximum-speed constraint binds, route comparison uses a
separately labeled vehicle-adjusted time for time ranking and driver cost while
continuing to show OSRM's duration as the primary provider estimate. No speed
value is inferred when the field is missing.

## Fleet CSV/XLSX format

Download the blank [fleet-data-template.csv](fleet-data-template.csv) from the
planner. CSV and `.xlsx` uploads are supported. `company_id` groups rows into
profiles; `vehicle_id` and `capacity` are required columns and values. Vehicle
IDs must be unique within a company profile. Other columns are optional; blank
values remain unknown, are reported in the preview, and are never filled with
invented details.

| Column | Requirement and use |
| --- | --- |
| `vehicle_id` | Required; unique within a company profile. |
| `capacity` | Required; finite positive cargo capacity in user-supplied units. |
| `company_id` | Optional; company/fleet name or ID used to group selectable profiles. Blank IDs are shown as unspecified, not replaced with an inferred company. |
| `vehicle_type` | Optional descriptive label shown alongside the vehicle ID; not a route constraint. |
| `max_speed_kmph` | Optional finite positive maximum speed. OSRM duration remains primary; if the limit implies a longer minimum travel time, the UI labels that separately as a vehicle-adjusted estimate and uses it for time ranking and driver-cost estimates. |
| `available` | Optional boolean (`true`/`false`, `yes`/`no`, `1`/`0`, `available`/`unavailable`). Explicitly unavailable vehicles cannot be selected. Blank means availability is unknown and is called out in the UI. `availability` and `status` are accepted aliases. |
| `fuel_type` | Optional: Diesel, Gasoline (Petrol is accepted as an alias and calculated with the Gasoline model), or Electric. CNG and LPG are distinct but unsupported: this model has no configured consumption units or emissions factors for either. |
| `fuel_consumption_per_km` | Optional, finite positive; Diesel and Gasoline/Petrol values are in L/km; Electric values are in kWh/km. Used for estimated quantity and available fuel/tailpipe metrics. `fuel_efficiency` and `fuel_use_per_km` are accepted aliases. |
| `fuel_price_per_unit` | Optional, non-negative, in the uploaded cost units per the fuel unit implied by `fuel_type` (per L for Diesel/Gasoline/Petrol or per kWh for Electric); multiplied by estimated fuel/electricity quantity. No price is inferred and no currency conversion occurs. `fuel_price` is an accepted alias. |
| `currency` | Optional 3-letter code attached to displayed cost values; it is not used for currency conversion and no default currency is applied. |
| `driver_cost_per_hour` | Optional, non-negative; multiplied by OSRM duration (or the separately labeled vehicle-adjusted estimate when maximum speed binds). |
| `shift_start_min`, `shift_end_min` | Optional integer minutes from midnight, 0–1440; displayed as driver-shift information. Shift feasibility is not calculated because this point-to-point planner has no departure-time schedule. `shift_start` and `shift_end` are accepted aliases. |
| `available_fuel_quantity` | Optional, non-negative, in the unit implied by `fuel_type` (L for Diesel/Gasoline/Petrol, kWh for Electric). When fuel type and consumption are present, may be explicitly enabled as a route quantity constraint. `available_fuel` is an accepted alias. |

Tailpipe estimates use the configured combustion factors of 2.68 kg CO2/L for
Diesel and 2.31 kg CO2/L for Gasoline/Petrol. Electric estimates use uploaded
kWh/km and uploaded cost/kWh for energy quantity and cost; the reported 0 kg
tailpipe CO2 is not a grid-electricity or lifecycle-emissions estimate. Grid
emissions are unavailable because no electricity factor is supplied. Fuel and
emissions values remain unavailable when the required uploaded inputs are
missing. These are the application's configured estimates, not a complete
well-to-wheel emissions assessment.

`vehicle_capacity` is an accepted alias for `capacity`. Other unlisted columns
are unsupported and cause validation to fail rather than being silently
ignored. CSV and `.xlsx` files are parsed before acceptance; a data preview is
shown first, and malformed files, missing required columns/values, invalid
numeric/availability values, duplicate IDs, and unsupported columns are
reported without accepting the upload. Errors identify the row and field but
do not echo values. The fleet stays in Streamlit session memory only, is not
written to SQLite history, and is never sent to Nominatim or OSRM. Use **Clear
session fleet profile** or close the session to remove it.

Capacity checking is an optional point-to-point cargo-demand comparison. It is
not a multi-stop assignment. Shift fields are not used as a route-feasibility
check here because a departure time, return route, and service schedule are not
part of a one-way OSRM route response.

## Honest integration boundaries

- OSRM road-route alternatives are routing-provider output and do not imply
  QUBO/QAOA optimization.
- Local Demo Mode continues to use its fixed locations and simulated traffic
  and fleet incidents. The custom planner does not change its behavior.
- Current browser location is read only after the explicit browser-button click
  and permission grant; it is held in the active Streamlit session.
- No live GPS fleet tracking, live traffic feed, quantum advantage, or real IBM
  hardware run is claimed.
