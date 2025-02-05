import csv
import os
from src.data.utils import get_file_names, concat_frames, csv_to_df
from src.data.plotlib import plot_distribution


def write_csv(old_path, new_path, mapping):
    '''
    Write new csv files to new_path with new class names and new columns.

    Args:
        old_path (str): path to the folder containing the old csv files
        new_path (str): path to the folder to save the new csv files
        mapping (Dict): dictionary mapping old class names to new class names
    '''
    # get all file names in the old csv files folder sorted alphabetically
    old_csv_file_names = get_file_names(old_path, 'csv')
    if not os.path.exists(new_path):
        os.makedirs(new_path)

    # loop through all the csv files
    for idx in range(len(old_csv_file_names)):
        # get new file name to save new dataframe
        new_csv_file_name = new_path + old_csv_file_names[idx].split('/')[-1]

        # read in old dataframe and have columns renamed and changed
        frame = csv_to_df(old_csv_file_names[idx])
        frame['class_name'] = frame['desc'].map(mapping).fillna(frame['desc'])
        frame['xmin'] = frame['x']
        frame['ymin'] = frame['y']
        frame['xmax'] = frame['x'] + frame['width']
        frame['ymax'] = frame['y'] + frame['height']

        # drop old columns
        frame = frame.drop('desc', axis=1)
        frame = frame.drop('class_id', axis=1)
        frame = frame.drop('x', axis=1)
        frame = frame.drop('y', axis=1)
        frame = frame.drop('width', axis=1)
        frame = frame.drop('height', axis=1)

        # save new dataframe to csv file
        with open(new_csv_file_name, 'w') as f:
            frame.to_csv(f, index=False)

    # print that all csv files have been written
    print('Finished writing csv files')


def add_class_id_and_data_exploration(new_path, title, data_path, plot_path=None):
    '''
    Add class_id column to each csv file and plot distribution of classes.

    Args:
        new_path (str): Path to the folder containing the new csv files
        title (str): Title of the plot
        data_path (str): Path to the folder to save the class_id_mapping.csv file
        plot_path (str, optional): Path to the folder to save the plot. Default is None.
    '''
    # get all file names in the new csv files folder sorted alphabetically
    new_csv_file_names = get_file_names(new_path, 'csv')

    # concat all the dataframes into one
    contact_frame = concat_frames(new_csv_file_names)
    val_counts = contact_frame['class_name'].value_counts()
    print('Number of classes: ', len(val_counts))
    # print(val_counts)

    # add class_id column
    sorted_class_name = sorted(contact_frame['class_name'].unique())  # alphabetically sorted
    class_id_mapping = {item: index for index, item in enumerate(sorted_class_name)}
    # print('class_id_mapping: ', class_id_mapping)

    # save class_id_mapping to csv file
    with open(data_path + 'class_id.csv', 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['class_name', 'class_id'])
        for key, value in class_id_mapping.items():
            writer.writerow([key, value])

    # add class_id column to each csv file
    for new_csv_file_name in new_csv_file_names:
        frame = csv_to_df(new_csv_file_name)
        frame['class_id'] = frame['class_name'].map(class_id_mapping)
        frame = frame.reindex(sorted(frame.columns), axis=1)
        with open(new_csv_file_name, 'w') as f:
            frame.to_csv(f, index=False)

    # plot distribution of classes
    _ = plot_distribution(contact_frame, 'class_name', 'class', 'count', title, plot_path)
    print("Finished adding class_id column and plotting distribution of classes")
