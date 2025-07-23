import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from exactextract import exact_extract


def exact_raster_poly_extract(
        raster_path,
        shp_path,
        include_cols=None,
        out_path=None,
        fillvalue=9999,
        return_df=True,
        progress=True):
    '''
    Robustly explode the exact_extract output.
    Transforms a df of polygons x bands (where each cell contains 1D arrays of pixel values) into a df of
    pixels x bands + metadata.

    Code currently only works if values are distributed identically over the pixels. i.e.
    all bands have either values of na for a given pixel. -> identical nodata patterns
    '''

    polygons = gpd.read_file(shp_path)
    geometry_col = 'geometry'

    # Convert columns to digestible types for exact_extract
    for col in polygons.columns:
        if col == geometry_col:
            continue
        try:
            # Check if integer-only
            if polygons[col].dropna().apply(float.is_integer).all():
                polygons[col] = polygons[col].astype("Int64")
            else:
                # Try numeric (floats)
                polygons[col] = pd.to_numeric(polygons[col], errors="coerce").astype(float)
        except (ValueError, TypeError):
            # Fallback to string
            polygons[col] = polygons[col].astype(str)

        # Fill any remaining NaNs and warn
        if polygons[col].isnull().any():
            print(f"Column '{col}' contained missing values; filling with {fillvalue}.")
            polygons[col] = polygons[col].fillna(fillvalue)

    # read raster metadata
    with rasterio.open(raster_path) as src:
        num_bands = src.count
        bounds = src.bounds
        raster_crs = src.crs

    bandnames = [f"B_{i}" for i in range(1, num_bands + 1)]

    # ensure matching crs!
    if raster_crs != polygons.crs:
        polygons = polygons.to_crs(src.crs)
        print("CRS did not match. Shapefile CRS has been reprojected.")

    # Crop polygon to raster extent
    polygons = gpd.clip(polygons, bounds)

    if include_cols is None:
        include_cols = polygons.columns.tolist()

    # Run exact_extract
    ex_ex_df = exact_extract(raster_path, polygons, ["values", "coverage"],
                             include_cols=include_cols, output="pandas", progress=progress)

    # Remove empty fields
    ex_ex_df = ex_ex_df[ex_ex_df.iloc[:, len(include_cols) + 1].apply(lambda x: len(x) > 0)]

    if ex_ex_df.empty:
        raise ValueError("No polygons were found in the raster extent.")

    # Identify columns
    meta_count = len(include_cols)  # number of cols transfered from polygon df
    pixelwise_cols = ex_ex_df.columns[meta_count:num_bands + meta_count]
    fraction_cols = [c for c in ex_ex_df.columns if
                     c.endswith("_coverage")]  # id of cols containing actual pixel values

    # Check that all bands have identical coverage (same number of pixels)
    def _check_coverage_consistency(row):
        """
        Check if all coverage arrays have the same length

        This is to catch a inconsistency in the exact_extract output when bands/layers have differing nodata patterns.
        If a layer does not have as many valid (non nodata) pixels as the others, it will output fewer values leading to differing array lengths.
        This in turn can cause issues when stacking values into a pixelwise df.
        Would need a solution in the long term.

        We are just trying to catch this before any data is misaligned unintentionally.
        Not of importance for standard non composited multi band satellite imagery.
        """
        coverage_arrays = []
        lengths = []
        for col in fraction_cols:
            val = row[col]
            if isinstance(val, (list, np.ndarray)):
                coverage_arrays.append(np.array(val))
                lengths.append(len(val))
            else:
                # Single value case
                coverage_arrays.append(np.array([val]))
                lengths.append(1)

        # Check if all arrays have the same length
        if len(set(lengths)) != 1:
            # Get value arrays for better error message
            value_lengths = []
            for col in pixelwise_cols:
                val = row[col]
                value_lengths.append(len(val) if hasattr(val, '__len__') else 1)
            poly_idx = row.name  # <-- Get the index from the row itself

            raise ValueError(
                f"Inconsistent pixel counts across bands for polygon {poly_idx}.\n"
                f"This usually occurs when bands have different nodata patterns.\n"
                f"Values per bands for polygon: {poly_idx} : {value_lengths}\n"
                f"Consider preprocessing your raster to ensure all bands have identical nodata patterns."
            )

        # All same length - can stack and take mean
        return np.nanmean(np.stack(coverage_arrays), axis=0)

    mean_coverages = ex_ex_df.apply(_check_coverage_consistency, axis=1)

    metadata = ex_ex_df[include_cols]
    pixelvalues = ex_ex_df[pixelwise_cols]

    # Pre-allocate lists for efficient construction
    all_data_rows = []

    # Process each polygon
    for poly_idx in range(len(ex_ex_df)):
        # Get pixel values for all bands for this polygon
        pixel_arrays = [pixelvalues.iloc[poly_idx, j] for j in range(len(pixelwise_cols))]

        # Verify all bands have the same number of pixels
        pixel_lengths = [len(arr) for arr in pixel_arrays]
        if len(set(pixel_lengths)) != 1:
            raise ValueError(  # this is a fallback test. should never be called
                f"Polygon {poly_idx} has inconsistent pixel counts across bands: {pixel_lengths}. "
                f"This should have been caught earlier."
            )

        n_pixels = pixel_lengths[0]

        # Get metadata for this polygon
        poly_metadata = metadata.iloc[poly_idx]

        # Get coverage values for this polygon
        coverage_values = mean_coverages.iloc[poly_idx]
        if isinstance(coverage_values, (int, float)):
            coverage_values = [coverage_values]

        # Build rows for all pixels in this polygon
        for pix_idx in range(n_pixels):
            row_data = {}  # use dict  to collect data

            # Add metadata columns
            for col in include_cols:
                row_data[col] = poly_metadata[col]

            # Add coverage fraction
            row_data['cover_frac'] = coverage_values[pix_idx]

            # Add pixel ID within polygon
            row_data['polyPxID'] = pix_idx + 1

            # Add band values
            for band_idx, band_name in enumerate(bandnames):
                row_data[band_name] = pixel_arrays[band_idx][pix_idx]

            all_data_rows.append(row_data)

    # Create final dataframe from list of dictionaries
    final_result = pd.DataFrame(all_data_rows)

    # Reorder columns to have metadata first, then cover_frac, polyPxID, then bands
    col_order = list(include_cols) + ['cover_frac', 'polyPxID'] + bandnames
    final_result = final_result[col_order]

    if out_path:
        final_result.to_csv(out_path, index=False)

    print(f"Done! Processed {len(ex_ex_df)} polygons into {len(final_result)} pixel rows.")

    if return_df:
        return final_result