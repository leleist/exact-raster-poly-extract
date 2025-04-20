from setuptools import setup

setup(
    name='exact_raster_poly_extract',
    version='1.0.0',
    license='Apache 2.0',
    install_requires=[
        'pandas>=2.2.3',
        'geopandas>=1.0.1',
        'rasterio>=1.4.3',
        'exactextract>=0.2.1'
    ],
)

