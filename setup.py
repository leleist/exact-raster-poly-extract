from setuptools import setup

setup(
    name='exact_raster_poly_extract',
    version='1.0.1',
    license='Apache 2.0',
    packages=['exact_raster_poly_extract'],
    install_requires=[
        'pandas>=2.2.3',
        'geopandas>=1.0.1',
        'rasterio>=1.4.3',
        'exactextract>=0.2.1'
    ],
)

