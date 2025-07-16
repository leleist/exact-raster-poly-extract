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
    robustly, not necessarily efficiently, explode the exact_extract output.
    output is a df of polygons x bands, where each cell contains a list(pd.series) of pixel values.
    The length of said lists varies with the number of pixels in the polygon.

    The goal is to create a df with pixels x bands + metadata
    The polygon information should be wrapped into a separate column.
'''

    # TODO: make polygonID field an extra parameter, then combine the col with the others. if no polygonID is given, generate one!

    polygons = gpd.read_file(shp_path)

    # convert any pd.series of object type to numeric or string types to make it digestible for exact_extract
    geometry_col = 'geometry'

    for col in polygons.columns:
        if col != geometry_col:
            try:
                if polygons[col].dropna().apply(float.is_integer).all():
                    polygons[col] = polygons[col].astype('Int64')
                else:
                    polygons[col] = pd.to_numeric(polygons[col], errors='coerce').astype(float)
            except (ValueError, TypeError):
                polygons[col] = polygons[col].astype(str)
                polygons[col] = polygons[col].replace('nan', int(0)) # np.nan

    for col in polygons.columns:
        if col == geometry_col:
            continue
        try:
            # integer‐only?
            if polygons[col].dropna().apply(float.is_integer).all():
                polygons[col] = polygons[col].astype("Int64")
            else:
                # try numeric (floats)
                polygons[col] = pd.to_numeric(polygons[col], errors="coerce").astype(float)
        except (ValueError, TypeError):
            # fallback to string
            polygons[col] = polygons[col].astype(str)

        # fill any remaining NaNs (or <NA>) and warn
        if polygons[col].isnull().any():
            print(f"Column '{col}' contained missing values; filling with {fillvalue}.")
            polygons[col] = polygons[col].fillna(fillvalue)

    with rasterio.open(raster_path) as src:
        raster = src.read()
        bounds = src.bounds

    num_bands = raster.shape[0]  # - 1
    # Create a list of the band names starting from 1
    bandnames = [f"B_{i}" for i in range(1, num_bands + 1)]

    if src.crs != polygons.crs:
        polygons = polygons.to_crs(src.crs)
        print("Crs did not match. .shp crs has been reprojected.")

    # crop polygon to raster extent
    polygons = gpd.clip(polygons, bounds)

    if include_cols is None:
        include_cols = polygons.columns.tolist()

    # Check if any columns in include_cols are of object type
    # TODO add check and conversion to ensure input compatibility with exact_extract
    # if any(isinstance(col, object) for col in include_cols):
    #     print("include_cols has object dtypes. Converting to str.")
    #     include_cols = [str(col) for col in include_cols]w

    ex_ex_df = exact_extract(raster_path, polygons, ["values", "coverage"],
                             include_cols=include_cols, output="pandas", progress=progress)
    # TODO add include geometry option (see documentation)

    # remove empty fields
    ex_ex_df = ex_ex_df[ex_ex_df.iloc[:, len(include_cols) + 1].apply(lambda x: len(x) > 0)]

    if ex_ex_df.empty:
        raise ValueError("No polygons were found in the raster extent.")

    # preparation
    meta_count = len(include_cols)
    pixelwise_cols = ex_ex_df.columns[
                     meta_count:num_bands + meta_count]  # skip the metadata cols, just pixel values/bands

    # take mean over redundant/identical band wise coverage columns, yet retain redundancy should some bands have NAs.
    fraction_cols = [c for c in ex_ex_df.columns if c.endswith("_coverage")]
    mean_coverages = ex_ex_df[fraction_cols].apply(
        lambda row: np.nanmean(np.stack(row.values), axis=0),
        axis=1
    )


    metadata = ex_ex_df[include_cols]
    pixelvalues = ex_ex_df[pixelwise_cols]
    fractions = ex_ex_df[fraction_cols]

    final_result = pd.DataFrame()

    # transforming df (slow but robust approach)
    for i in range(pixelvalues.shape[0]):  # polygons i.e. fields
        bands_per_polygon = pd.DataFrame()  # collects pixel*bands df for each polygon

        for array in pixelvalues.iloc[i, :]:  # bands
            df_from_array = pd.DataFrame(array)
            bands_per_polygon = pd.concat([bands_per_polygon, df_from_array], axis=1)

        bands_per_polygon.columns = bandnames

        # coverage fractions
        polygon_fractions = pd.DataFrame(fractions.iloc[i]) # 1D array, one value per pixel

        specific_metadata = metadata.iloc[i, :]
        specific_metadata_rep = [specific_metadata] * len(array)

        colnames = metadata.columns.tolist()

        # TODO: ADD in robus polygon pixel ID
        # The below implementation works in generating the lists, but the concat fails, creating NANs for all values
        # create a running number for each pixel in a given field
        # field_PxIDs = list(range(1, len(array) + 1))
        # bands_per_polygon.insert(0, "polyPxID", field_PxIDs)

        # previous implementationwhich was unsable
        #for i, colname in enumerate(colnames):
        #    bands_per_polygon.insert(i, colname, [specific_metadata[colname]] * len(array))


        for col_idx, colname in enumerate(colnames):
            bands_per_polygon.insert(col_idx, colname, [specific_metadata[colname]] * len(bands_per_polygon))


        bands_per_polygon.insert(meta_count, "cover_frac", polygon_fractions)

        final_result = pd.concat([final_result, bands_per_polygon], axis=0).reset_index(drop=True, inplace=False)
    print("Done")
    return final_result

