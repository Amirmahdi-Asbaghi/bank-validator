from rest_framework import serializers


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, f):
        name = f.name.lower()
        if not (name.endswith(".csv") or name.endswith(".json")):
            raise serializers.ValidationError("Only .csv and .json files are accepted.")
        if f.size > 200 * 1024 * 1024:
            raise serializers.ValidationError("File exceeds the 200 MB limit.")
        return f