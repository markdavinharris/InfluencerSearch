# Test fixtures

Raw API responses used by the normalization tests.

The first versions were written by hand from each API's documented response format
(the build sandbox could not reach the platforms). Replace them with real recordings as
soon as credentials exist:

    uv run python -m app.jobs.collect --topic pa-energy --platform bluesky --dry-run \
        --record tests/fixtures/recorded/

then trim a recorded file to a few representative items and save it over the matching
fixture here. Keep the file names; the tests load them by name.
