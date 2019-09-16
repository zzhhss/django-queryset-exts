import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="django-queryset-exts",
    version="0.0.1",
    author="zzhhss",
    author_email="clayhaw@163.com",
    description="A django queryset with a select_related(or prefetch_related) like method to fetch remote(e.g., api) data.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/zzhhss/django-queryset-exts",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)
