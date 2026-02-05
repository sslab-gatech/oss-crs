# Use Python 3.12 slim as the base image
FROM python:3.12-slim
COPY ./main.py /main.py
COPY ./requirements.txt /requirements.txt

RUN pip install -r /requirements.txt

CMD ["/main.py"]
